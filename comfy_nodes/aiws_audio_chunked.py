r"""
AI 音乐工坊 —— 分块音频解码节点
==================================
解决的问题：
  ComfyUI 原生的 VAEDecodeAudio 是**整段一次性解码**（内部调 vae.decode(latent)），
  长音频（实测 170 秒）会让 C 层内存分配失败，直接
      Fatal Python error: Aborted
  整个 ComfyUI 进程崩掉。

  之前的土办法是「把歌词拆开、分几段各自生成、再拼起来」——
  但每段是**独立生成**的，调性/速度/乐器/相位全对不上，
  拼接口会出现爆音和明显的割裂感（用户反馈的「爆破不协调」）。

本节点的做法：
  一次生成完整长度（音乐是连贯的），只在**解码**时把 latent 沿时间轴切块，
  每块多带一点上下文（context），解出来只取中间那段，最后拼起来。
  VAE 是卷积（感受野局部），所以加了 context 之后接缝基本听不出来。
  最后整体做一次归一化 —— 绝不分块各自归一化，否则音量会一跳一跳。

安装：把本文件丢进  ComfyUI\custom_nodes\  重启 ComfyUI 即可。
"""

import torch

import comfy.model_management
import node_helpers
from comfy_api.latest import ComfyExtension, IO


# ACE-Step 1.5 的 latent 时间轴密度：
#   看 EmptyAceStep15LatentAudio 的实现 —— length = round(seconds * 48000 / 1920)
#   也就是每秒 25 个 latent 帧。
LATENT_FPS = 25.0


def _trim_tail(audio, rate, keep_seconds=0.6):
    """裁掉末尾**连续**的静音（保守：宁可留白，也绝不误删）

    ★ 踩过大坑：原来用「最后一个有声采样点」判断 ✗
      有首歌后半段整体音量偏轻，结果把 **91 秒**当静音裁掉，
      120 秒的歌只剩 **29 秒** ✗✗（主人发现）
    改成三条保险：
      ① 从末尾往前数**连续**低于阈值的样本，一遇到有声就停 ✓
      ② 阈值取「峰值×1%」与 **0.002(-54dBFS)** 的**较小值**，绝不偏高 ✓
      ③ 要裁的部分**超过总长 30% 就放弃**（那么长多半是判错了）✓
    """
    n = audio.shape[-1]
    if n < rate:
        return audio
    x = audio.abs().amax(dim=1)[0]                  # [T]
    peak = float(x.max())
    if peak <= 1e-6:
        return audio
    thr = min(max(peak * 0.01, 1e-5), 0.002)
    i = n - 1
    while i >= 0 and float(x[i]) <= thr:
        i -= 1
    keep = min(n, (i + 1) + int(rate * keep_seconds))
    cut = n - keep
    if cut <= 0:
        return audio
    if cut > n * 0.3:
        print("[AIWS chunked] 末尾静音长达 %.1f 秒（占 %.0f%%），像是判错了 → **不裁**"
              % (cut / float(rate), 100.0 * cut / n), flush=True)
        return audio
    print("[AIWS chunked] 裁掉末尾连续静音 %.1f 秒" % (cut / float(rate)), flush=True)
    return audio[..., :keep]


def _soften(audio, rate, k):
    """按频谱诊断做柔和化。audio: [B, C, T]；k: 力度（0=关，1.5=重）

    诊断依据（100 秒那首实测）：能量全堆在 100~315 Hz，
    1~4 kHz 人声区比低频低 27 dB，而高频本身就偏少 ——
    所以**不是砍高频**（会更闷），而是削掉 315 Hz 凸起 + 把人声区提回来 ✓
    """
    if k <= 0:
        return audio
    n = audio.shape[-1]
    freq = torch.fft.rfftfreq(n, 1.0 / rate)
    safe = freq.clamp(min=1.0)
    g = torch.zeros_like(freq)
    g = g - 4.0 * k * torch.exp(-0.5 * ((torch.log2(safe / 315.0)) / 0.35) ** 2)
    g = g + (-2.0 * k) / (1.0 + (safe / 120.0) ** 4)
    g = g + (3.0 * k) * torch.exp(-0.5 * ((torch.log2(safe / 1800.0)) / 0.8) ** 2)
    g = g + (-1.5 * k) / (1.0 + (8000.0 / safe) ** 4)
    gain = 10.0 ** (g / 20.0)
    spec = torch.fft.rfft(audio, dim=-1)
    y = torch.fft.irfft(spec * gain, n=n, dim=-1)
    print("[AIWS chunked] 柔和化：力度 %.2f（315Hz -%.1fdB，1.8kHz +%.1fdB）"
          % (k, 4.0 * k, 3.0 * k), flush=True)
    return y


def _decode_chunked(vae, latent, chunk_seconds, context_seconds):
    """
    latent: [B, C, L] 张量
    返回拼好的 waveform（未归一化）

    ★ 这里踩过一个大坑（症状：生成出来**后半段全是杂音/没声音**，而且是"有概率"）：
      原来是在循环里算 `tdim`（判断时间轴在哪一维），循环外才 `torch.cat(dim=tdim)` ✗
      —— 用的是**最后一块**的判断结果。
      而最后一块常常 `e = min(L, end+ctx)` 吃不满上下文，解码长度对不上，
      判断就会翻转成另一维 ✗ → 拼错维度 → 前半段正常、后半段全乱。

      修法：**只按第一块定布局**，之后每块都先 movedim 成统一的 [B, 时间, 声道]，
            最后固定按 dim=1 拼 ✓ 另外切片长度按**实际**解码结果裁，避免越界变短。
    """
    rate = getattr(vae, "audio_sample_rate_output",
                   getattr(vae, "audio_sample_rate", 48000)) or 48000
    spf = rate / LATENT_FPS                     # 每个 latent 帧对应多少采样点
    L = latent.shape[-1]
    chunk = max(1, int(chunk_seconds * LATENT_FPS))
    ctx = max(0, int(context_seconds * LATENT_FPS))

    outs = []
    pos = 0
    tdim = None                                 # ★ 只认第一块定的布局，之后不再改
    n = 0
    while pos < L:
        end = min(L, pos + chunk)
        s = max(0, pos - ctx)
        e = min(L, end + ctx)
        dec = vae.decode(latent[..., s:e])
        n += 1

        if tdim is None:
            # 只看第一块：哪一维的长度最接近"预期采样点数"，哪一维就是时间轴
            want_all = int(round((e - s) * spf))
            cands = [(abs(dec.shape[d] - want_all), d)
                     for d in range(dec.dim()) if dec.shape[d] > 2]
            tdim = min(cands)[1] if cands else dec.dim() - 1
            print("[AIWS chunked] latent L=%d, 帧率=%.1f, 采样率=%d, 块=%d帧, 上下文=%d帧, "
                  "第一块解码形状=%s → 时间轴在第 %d 维"
                  % (L, LATENT_FPS, rate, chunk, ctx, tuple(dec.shape), tdim), flush=True)

        if tdim != 1:
            dec = dec.movedim(tdim, 1)          # 统一成 [B, 时间, 声道] ✓

        total_t = dec.shape[1]
        a0 = max(0, min(int(round((pos - s) * spf)), total_t))
        a1 = max(a0, min(a0 + int(round((end - pos) * spf)), total_t))
        outs.append(dec[:, a0:a1, ...])
        print("[AIWS chunked] 第 %d 块：latent[%d:%d] 解出 %d 帧，取 [%d:%d] = %d 采样点"
              % (n, s, e, total_t, a0, a1, a1 - a0), flush=True)
        pos = end

    if not outs:
        raise RuntimeError("分块解码：没有切出任何块")
    if len(outs) == 1:
        return outs[0]
    return torch.cat(outs, dim=1)               # ★ 固定按时间维拼 ✓


class AIWSVAEDecodeAudioChunked(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="AIWS_VAEDecodeAudioChunked",
            display_name="VAE Decode Audio (Chunked)",
            category="audio",
            description=("分块解码音频，避免长音频一次性解码导致的内存崩溃。"
                         "解出来的音乐是连贯的（一次生成），不会有分段拼接的割裂感。"),
            inputs=[
                IO.Latent.Input("samples"),
                IO.Vae.Input("vae"),
                IO.Float.Input("chunk_seconds", default=60.0, min=10.0, max=300.0,
                               step=5.0, tooltip="每块解码多少秒；越小越省内存"),
                IO.Float.Input("context_seconds", default=4.0, min=0.0, max=30.0,
                               step=1.0, tooltip="每块多带多少秒上下文用于消接缝；"
                                                 "接缝能听出来就调大"),
                IO.Float.Input("soften", default=1.5, min=0.0, max=3.0, step=0.1,
                               tooltip="柔和度：0=不处理，0.6=轻，1.0=中，1.5=重（默认）"),
            ],
            outputs=[IO.Audio.Output()],
        )

    @classmethod
    def execute(cls, vae, samples, chunk_seconds, context_seconds,
                soften=1.5) -> IO.NodeOutput:
        latent = samples["samples"]
        if latent.is_nested:
            latent = latent.unbind()[-1]

        audio = _decode_chunked(vae, latent, float(chunk_seconds), float(context_seconds))
        print("[AIWS chunked] movedim 之前：形状=%s（[B, 时间, 声道]）"
              % (tuple(audio.shape),), flush=True)
        audio = audio.movedim(-1, 1)
        print("[AIWS chunked] 交给 ComfyUI：形状=%s　latent 里带的 sample_rate=%s"
              % (tuple(audio.shape), samples.get("sample_rate")), flush=True)

        # ★ rate 必须先取出来 —— 柔和化要用它
        #   （踩过：把 _soften(audio, rate, …) 写在 rate 定义之前 → UnboundLocalError ✗）
        rate = getattr(vae, "audio_sample_rate_output",
                       getattr(vae, "audio_sample_rate", 44100))
        if "sample_rate" in samples:
            rate = samples["sample_rate"]

        # 整体只归一化一次（分块各自归一化会造成音量一跳一跳）
        std = torch.std(audio, dim=[1, 2], keepdim=True) * 5.0
        std[std < 1.0] = 1.0
        audio = audio / std

        # 柔和化：削 315Hz 盒子声 + 提 1~3kHz 人声（放在归一化之后、限幅之前）
        audio = _soften(audio, rate, float(soften))

        # ★ 兜底：按标准差归一化之后，副歌高潮仍可能冲到 1.0 以上 →
        #   FLAC 是定点格式，超了就**削顶**，听起来像破音 / 杂音 ✗
        #   所以再瞄一眼真峰值，超了就整体缩一点，留 1% 余量 ✓
        pk = float(audio.abs().max())
        if pk > 0.99:
            audio = audio * (0.99 / pk)
            print("[AIWS chunked] 峰值 %.3f 偏高，整体缩到 0.99 免得削顶" % pk, flush=True)

        # 裁掉末尾留白（ACE-Step 编完会空转，实测能留 12 秒）
        audio = _trim_tail(audio, rate)

        return IO.NodeOutput({"waveform": audio, "sample_rate": rate})


class AIWSAudioExt(ComfyExtension):
    async def get_node_list(self):
        return [AIWSVAEDecodeAudioChunked]


async def comfy_entrypoint() -> AIWSAudioExt:
    return AIWSAudioExt()
