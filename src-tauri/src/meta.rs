//! 音频元数据读取 + WAV 导出
//!
//! 全部走 symphonia（纯 Rust 解码），好处是一套代码同时拿到
//! 标签（ID3v2 / VorbisComment / MP4 ilst）、精确帧数和采样率，
//! 不需要为每种格式单独手写解析器，也不用把解码器塞进前端。

use serde::Serialize;
use std::io::{Seek, SeekFrom, Write};
use std::path::Path;

use symphonia::core::codecs::audio::{AudioCodecId, AudioDecoderOptions};
use symphonia::core::errors::Error;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, FormatReader, TrackType};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::{MetadataOptions, RawValue, StandardTag};

#[derive(Serialize, Default, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct TrackMeta {
    pub title: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    /// 秒
    pub duration: Option<f64>,
    /// 真实编码（FLAC / MP3 / PCM …），由容器探出，不看扩展名
    pub codec: Option<String>,
    pub sample_rate: Option<u32>,
    pub channels: Option<u32>,
    pub bits: Option<u32>,
    /// 由文件大小与时长反推，单位 kbps（估算值，只为显示）
    pub bitrate: Option<u32>,
}

/// 把 symphonia 的编码 ID 翻成短名字。
///
/// 用 ID 而不是扩展名：改过扩展名的文件（比如 FLAC 存成 .mp3）只有
/// 从容器里探出来才准。PCM 家族有四十多个 ID，按 0x100 段统一叫 PCM。
fn codec_name(id: AudioCodecId) -> Option<String> {
    use symphonia::core::codecs::audio::well_known::*;

    // Display 实现是 "{:#x}"，借它取回原始 ID 做区段判断
    let raw = u32::from_str_radix(id.to_string().trim_start_matches("0x"), 16).ok()?;
    if (0x100..0x200).contains(&raw) {
        return Some("PCM".to_string());
    }

    let name = if id == CODEC_ID_MP3 {
        "MP3"
    } else if id == CODEC_ID_MP2 {
        "MP2"
    } else if id == CODEC_ID_MP1 {
        "MP1"
    } else if id == CODEC_ID_FLAC {
        "FLAC"
    } else if id == CODEC_ID_ALAC {
        "ALAC"
    } else if id == CODEC_ID_VORBIS {
        "VORBIS"
    } else if id == CODEC_ID_OPUS {
        "OPUS"
    } else if id == CODEC_ID_SPEEX {
        "SPEEX"
    } else if id == CODEC_ID_AAC {
        "AAC"
    } else if id == CODEC_ID_WMA {
        "WMA"
    } else if id == CODEC_ID_MONKEYS_AUDIO {
        "APE"
    } else if id == CODEC_ID_WAVPACK {
        "WAVPACK"
    } else if id == CODEC_ID_AC3 {
        "AC3"
    } else {
        return None;
    };
    Some(name.to_string())
}

fn hint_for(path: &str) -> Hint {
    let mut hint = Hint::new();
    if let Some(ext) = Path::new(path).extension().and_then(|e| e.to_str()) {
        hint.with_extension(&ext.to_ascii_lowercase());
    }
    hint
}

fn open(path: &str) -> Option<Box<dyn FormatReader>> {
    let file = std::fs::File::open(path).ok()?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    symphonia::default::get_probe()
        .probe(
            &hint_for(path),
            mss,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .ok()
}

/// 标签值统一成字符串；非文本类型（封面二进制等）直接丢掉
fn raw_to_string(v: &RawValue) -> Option<String> {
    let s = match v {
        RawValue::String(s) => (**s).clone(),
        RawValue::StringList(list) => list.join(" / "),
        RawValue::UnsignedInt(n) => n.to_string(),
        RawValue::SignedInt(n) => n.to_string(),
        _ => return None,
    };
    let s = s.replace('\0', "").trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

/// 读单个文件的标签 / 时长 / 格式参数。
///
/// 打不开或格式不认识返回 `None`，前端就会退回用文件名显示——
/// 这类文件依旧能播（Chromium 侧解码），不要因为读不了标签就被过滤掉。
pub fn read_meta(path: &str) -> Option<TrackMeta> {
    let file_size = std::fs::metadata(path).ok()?.len();
    let mut format = open(path)?;
    let mut meta = TrackMeta::default();

    {
        let track = format.default_track(TrackType::Audio)?;
        if let Some(params) = track.codec_params.as_ref().and_then(|c| c.audio()) {
            meta.codec = codec_name(params.codec);
            meta.sample_rate = params.sample_rate.filter(|r| *r > 0);
            meta.channels = params
                .channels
                .as_ref()
                .map(|c| c.count() as u32)
                .filter(|c| *c > 0);
            meta.bits = params.bits_per_sample;
        }
        if let (Some(tb), Some(dur)) = (track.time_base, track.duration) {
            meta.duration = tb.calc_duration(dur).map(|t| t.as_secs_f64());
        }
        // 容器没给时长时，用帧数 / 采样率算（FLAC、WAV 的 STREAMINFO 都有精确帧数）
        if meta.duration.is_none() {
            if let (Some(nf), Some(sr)) = (track.num_frames, meta.sample_rate) {
                if nf > 0 {
                    meta.duration = Some(nf as f64 / sr as f64);
                }
            }
        }
    }

    {
        let mut md = format.metadata();
        if let Some(rev) = md.skip_to_latest() {
            for tag in &rev.media.tags {
                let Some(val) = raw_to_string(&tag.raw.value) else {
                    continue;
                };
                match &tag.std {
                    Some(StandardTag::TrackTitle(_)) => {
                        meta.title.get_or_insert(val);
                    }
                    Some(StandardTag::Artist(_)) => {
                        meta.artist.get_or_insert(val);
                    }
                    Some(StandardTag::Album(_)) => {
                        meta.album.get_or_insert(val);
                    }
                    _ => {}
                }
            }
        }
    }

    if let (Some(d), true) = (meta.duration, file_size > 0) {
        if d > 0.5 {
            meta.bitrate = Some((file_size as f64 * 8.0 / d / 1000.0).round() as u32);
        }
    }

    Some(meta)
}

/// 批量读元数据。前端在「加歌」后一次性丢进来，几百首也只占一次 IPC。
#[tauri::command]
pub async fn read_audio_meta(paths: Vec<String>) -> Vec<Option<TrackMeta>> {
    tauri::async_runtime::spawn_blocking(move || paths.iter().map(|p| read_meta(p)).collect())
        .await
        .unwrap_or_default()
}

/* ===========================================================
   WAV 导出
   =========================================================== */

fn wav_header(sample_rate: u32, channels: u16, bits: u16, data_len: u32) -> [u8; 44] {
    let byte_rate = sample_rate * channels as u32 * bits as u32 / 8;
    let block_align = channels * bits / 8;
    let mut h = [0u8; 44];
    h[0..4].copy_from_slice(b"RIFF");
    h[4..8].copy_from_slice(&(36u32.saturating_add(data_len)).to_le_bytes());
    h[8..12].copy_from_slice(b"WAVE");
    h[12..16].copy_from_slice(b"fmt ");
    h[16..20].copy_from_slice(&16u32.to_le_bytes());
    h[20..22].copy_from_slice(&1u16.to_le_bytes()); // PCM
    h[22..24].copy_from_slice(&channels.to_le_bytes());
    h[24..28].copy_from_slice(&sample_rate.to_le_bytes());
    h[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    h[32..34].copy_from_slice(&block_align.to_le_bytes());
    h[34..36].copy_from_slice(&bits.to_le_bytes());
    h[36..40].copy_from_slice(b"data");
    h[40..44].copy_from_slice(&data_len.to_le_bytes());
    h
}

/// 把任意受支持格式解码成 16bit PCM WAV。
///
/// 直接解码到目标码率（不重采样、不加淡入淡出、不经过均衡器）：
/// 导出的用途是「留一份干净的原始音质」，加了音效反而不对。
/// 返回 (时长秒, 文件字节数)。
pub fn export_wav(src: &str, dst: &str) -> Result<(f64, u64), String> {
    let mut format = open(src).ok_or_else(|| "无法识别的音频格式".to_string())?;

    let (track_id, codec_params) = {
        let track = format
            .default_track(TrackType::Audio)
            .ok_or_else(|| "文件里没有音频轨道".to_string())?;
        let params = track
            .codec_params
            .as_ref()
            .and_then(|c| c.audio())
            .ok_or_else(|| "缺少编码参数".to_string())?;
        (track.id, params.clone())
    };

    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(&codec_params, &AudioDecoderOptions::default())
        .map_err(|e| format!("不支持的编码：{e}"))?;

    let file = std::fs::File::create(dst).map_err(|e| format!("无法写入目标文件：{e}"))?;
    let mut out = std::io::BufWriter::new(file);
    out.write_all(&[0u8; 44])
        .map_err(|e| format!("写文件失败：{e}"))?;

    let mut pcm: Vec<f32> = Vec::new();
    let mut bytes: Vec<u8> = Vec::new();
    let mut total_frames: u64 = 0;
    let mut sample_rate = codec_params.sample_rate.unwrap_or(44100);
    let mut channels = codec_params
        .channels
        .map(|c| c.count())
        .unwrap_or(2)
        .max(1) as u16;

    loop {
        let packet = match format.next_packet() {
            Ok(Some(p)) => p,
            Ok(None) => break,
            // 链式 OGG：片段换了轨，按结束处理，已解码的部分照样有效
            Err(Error::ResetRequired) => break,
            Err(Error::IoError(_)) => break,
            Err(e) => return Err(format!("读取失败：{e}")),
        };
        if packet.track_id != track_id {
            continue;
        }

        let decoded = match decoder.decode(&packet) {
            Ok(d) => d,
            // 单个包坏了就跳过，别让整首导出失败
            Err(Error::DecodeError(_)) | Err(Error::IoError(_)) => continue,
            Err(Error::ResetRequired) => break,
            Err(e) => return Err(format!("解码失败：{e}")),
        };

        let spec = decoded.spec();
        sample_rate = spec.rate();
        channels = (spec.channels().count() as u16).max(1);

        pcm.clear();
        decoded.copy_to_vec_interleaved::<f32>(&mut pcm);
        if pcm.is_empty() {
            continue;
        }

        bytes.clear();
        bytes.reserve(pcm.len() * 2);
        for s in &pcm {
            let v = (s.clamp(-1.0, 1.0) * 32767.0).round() as i16;
            bytes.extend_from_slice(&v.to_le_bytes());
        }
        out.write_all(&bytes)
            .map_err(|e| format!("写文件失败：{e}"))?;

        total_frames += (pcm.len() / channels as usize) as u64;
    }

    out.flush().map_err(|e| format!("写文件失败：{e}"))?;
    let mut file = out
        .into_inner()
        .map_err(|e| format!("写文件失败：{}", e.error()))?;

    if total_frames == 0 {
        return Err("没有解出任何音频数据".to_string());
    }

    let data_len_u64 = total_frames * channels as u64 * 2;
    if data_len_u64 > u32::MAX as u64 {
        return Err("文件超过 4GB，WAV 装不下".to_string());
    }
    let header = wav_header(sample_rate, channels, 16, data_len_u64 as u32);
    file.seek(SeekFrom::Start(0))
        .and_then(|_| file.write_all(&header))
        .map_err(|e| format!("回填文件头失败：{e}"))?;

    let duration = total_frames as f64 / sample_rate as f64;
    Ok((duration, 44 + data_len_u64))
}

#[tauri::command]
pub async fn export_wav_file(src: String, dst: String) -> Result<serde_json::Value, String> {
    let (duration, bytes) = tauri::async_runtime::spawn_blocking(move || export_wav(&src, &dst))
        .await
        .map_err(|e| format!("导出线程异常：{e}"))??;

    Ok(serde_json::json!({ "duration": duration, "bytes": bytes }))
}
