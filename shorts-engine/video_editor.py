import subprocess
import os
import logging
import uuid
import textwrap
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

class VideoEditor:
    def __init__(self, whisper_model_size: str = "base"):
        self.model = WhisperModel(whisper_model_size, device="cpu", compute_type="int8")

    def transcribe_audio(self, audio_path: str):
        logger.info(f"Transcribing audio: {audio_path}")
        segments, info = self.model.transcribe(audio_path, beam_size=5)
        return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]

    def generate_srt(self, segments, srt_path):
        def fmt(s):
            h, m = int(s // 3600), int((s % 3600) // 60)
            sr = s % 60
            return f"{h:02}:{m:02}:{int(sr):02},{int((sr - int(sr)) * 1000):03}"
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments):
                f.write(f"{i+1}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text']}\n\n")

    def _wrap_text_for_ffmpeg(self, text: str, max_chars: int = 35) -> str:
        """Wrap text at word boundaries (no escaping needed for textfile)."""
        lines = textwrap.wrap(text, width=max_chars)
        return "\n".join(lines)

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                capture_output=True, text=True, check=True
            )
            return float(result.stdout.strip())
        except Exception:
            return 5.0  # fallback

    def create_short(self, background_video: str, audio_path: str, output_path: str,
                     music_path: str = None, music_volume: float = 0.2,
                     logo_path: str = None, logo_position: str = "top-right"):
        srt_path = f"temp_subs_{uuid.uuid4().hex[:8]}.srt"
        segments = self.transcribe_audio(audio_path)
        self.generate_srt(segments, srt_path)

        style = "FontSize=10,PrimaryColour=&H00FFFF,Alignment=2,OutlineColour=&H000000,BorderStyle=1,Outline=1"
        sub_filter = f"subtitles={srt_path}:force_style='{style}'"

        inputs = ['-stream_loop', '-1', '-i', background_video, '-i', audio_path]
        filter_complex = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,{sub_filter}[v]"
        audio_mix = "[1:a]volume=1.0[voice]"

        if music_path:
            inputs.extend(['-stream_loop', '-1', '-i', music_path])
            audio_mix += f";[2:a]volume={music_volume}[bgmusic];[voice][bgmusic]amix=inputs=2:duration=first[a]"
        else:
            audio_mix += ";[voice]anull[a]"

        if logo_path:
            inputs.extend(['-i', logo_path])
            logo_index = 3 if music_path else 2
            pos_map = {
                "top-right": "main_w-overlay_w-20:20",
                "top-left": "20:20",
                "bottom-right": "main_w-overlay_w-20:main_h-overlay_h-20",
                "bottom-left": "20:main_h-overlay_h-20"
            }
            overlay_pos = pos_map.get(logo_position, "main_w-overlay_w-20:20")
            filter_complex += f";[{logo_index}:v]scale=200:-1[logo];[v][logo]overlay={overlay_pos}[vfinal]"
        else:
            filter_complex += ";[v]null[vfinal]"

        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex', f"{audio_mix};{filter_complex}",
            '-map', '[vfinal]', '-map', '[a]',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
            '-c:a', 'aac', '-b:a', '128k',
            '-shortest', output_path
        ]

        logger.info(f"Running FFmpeg for /render endpoint")
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return output_path
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed: {e.stderr.decode()}")
            raise Exception("FFmpeg error")
        finally:
            if os.path.exists(srt_path):
                os.remove(srt_path)

    def assemble_storyboard(self, scenes, output_path, music_path=None, music_volume=0.2):
        """
        Ensambla escenas con transiciones crossfade (fundido) entre clips.
        scenes: list of {audio, video, text, sub_pos, sub_size}
        """
        temp_clips = []
        temp_text_files = []
        FADE_DURATION = 0.5  # seconds for crossfade
        import time
        render_id = str(int(time.time()))

        try:
            for idx, scene in enumerate(scenes):
                clip_output = os.path.join("storage", "shorts", f"tmp_{render_id}_{idx}.mp4")
                
                # Get audio duration to sync zoom
                audio_duration = self._get_audio_duration(scene["audio"])
                fps = 25
                d_frames = int(max(1, audio_duration * fps))

                if not scene.get("video"):
                    raise Exception(f"La escena {idx+1} no tiene un vídeo o imagen de fondo asignado.")

                is_image = scene["video"].lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
                
                # Normalize Subtitle Position
                pos_val = str(scene.get("sub_pos", "5")).lower()
                y_pos = "(h-th)/2" # center default
                if pos_val in ["8", "bottom"]:
                    y_pos = "h-th-60"
                elif pos_val in ["2", "top"]:
                    y_pos = "60"
                
                # Normalize Subtitle Size
                size_map = {"small": 32, "medium": 48, "large": 72}
                raw_size = scene.get("sub_size", 48)
                sub_size = size_map.get(str(raw_size).lower(), raw_size)
                try:
                    sub_size = int(sub_size)
                except:
                    sub_size = 48

                char_width = sub_size * 0.55
                max_chars = int(864 / char_width) if char_width > 0 else 30
                wrapped_text = self._wrap_text_for_ffmpeg(scene["text"], max_chars=max_chars)
                
                txt_output = os.path.join("storage", "shorts", f"tmp_text_{render_id}_{idx}.txt")
                with open(txt_output, "w", encoding="utf-8") as f:
                    f.write(wrapped_text)
                temp_text_files.append(txt_output)
                
                tune_settings = ['-tune', 'stillimage'] if is_image else []

                if is_image:
                    inputs = ['-loop', '1', '-i', scene["video"]]
                    # Smooth zoom dynamic speed: target 1.3 in audio_duration
                    zoom_speed = 0.3 / d_frames if d_frames > 0 else 0.001
                    video_filter = (
                        "scale=1080:1920:force_original_aspect_ratio=increase,"
                        "crop=1080:1920,"
                        f"zoompan=z='zoom+{zoom_speed:.5f}':d={d_frames}:s=1080x1920,setsar=1"
                    )
                else:
                    inputs = ['-stream_loop', '-1', '-i', scene["video"]]
                    video_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

                escaped_txt_output = txt_output.replace("\\", "/") # Escaping path for FFmpeg
                drawtext = (
                    f"drawtext=textfile='{escaped_txt_output}'"
                    f":fontcolor=white:fontsize={sub_size}"
                    f":box=1:boxcolor=black@0.6:boxborderw=15"
                    f":x=(w-text_w)/2:y={y_pos}"
                    f":line_spacing=10:fix_bounds=true"
                )

                cmd = ['ffmpeg', '-y'] + inputs + [
                    '-i', scene["audio"],
                    '-vf', f"{video_filter},{drawtext},format=yuv420p,fps={fps}",
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p'
                ] + tune_settings + [
                    '-c:a', 'aac', '-ar', '44100',
                    '-map', '0:v', '-map', '1:a',
                    '-shortest', clip_output
                ]

                logger.info(f"Rendering scene {idx+1}/{len(scenes)} (Dur: {audio_duration:.2f}s)")
                subprocess.run(cmd, check=True, capture_output=True)
                temp_clips.append(clip_output)

            # --- Assemble with crossfade transitions ---
            if len(temp_clips) == 1:
                if music_path:
                    self._add_global_music(temp_clips[0], music_path, music_volume, output_path)
                else:
                    import shutil
                    shutil.copy(temp_clips[0], output_path)
                return output_path

            # Build xfade chain for smooth dissolve between clips
            # Get durations of each clip (needed for xfade offset calculation)
            durations = [self._get_audio_duration(c) for c in temp_clips]
            
            assembled = temp_clips[0]
            for i in range(1, len(temp_clips)):
                next_clip = temp_clips[i]
                merged = os.path.join("storage", "shorts", f"mrg_{render_id}_{i}.mp4")
                
                # offset = sum of durations so far minus fade duration
                offset = sum(durations[:i]) - FADE_DURATION * i
                offset = max(0.1, offset)

                cmd_fade = [
                    'ffmpeg', '-y',
                    '-i', assembled,
                    '-i', next_clip,
                    '-filter_complex',
                    f"[0:v]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS,fps=25[v0];"
                    f"[1:v]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS,fps=25[v1];"
                    f"[v0][v1]xfade=transition=fade:duration={FADE_DURATION}:offset={offset:.3f}[vout];"
                    f"[0:a][1:a]acrossfade=d={FADE_DURATION}[aout]",
                    '-map', '[vout]', '-map', '[aout]',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-ar', '44100',
                    merged
                ]
                subprocess.run(cmd_fade, check=True, capture_output=True)
                
                # Clean up intermediate
                if assembled != temp_clips[0] and os.path.exists(assembled):
                    os.remove(assembled)
                assembled = merged
                temp_clips.append(merged)  # Track for cleanup

            # Add global music if provided
            if music_path:
                self._add_global_music(assembled, music_path, music_volume, output_path)
            else:
                import shutil
                shutil.copy(assembled, output_path)

            if assembled != output_path and os.path.exists(assembled):
                os.remove(assembled)

            return output_path

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            logger.error(f"FFmpeg storyboard error: {stderr}")
            raise Exception(f"FFmpeg error: {stderr}")
        finally:
            # Clean original temp clips and text files
            for c in temp_clips + temp_text_files:
                if os.path.exists(c) and c != output_path:
                    try:
                        os.remove(c)
                    except Exception:
                        pass

    def _add_global_music(self, video_path, music_path, volume, output_path):
        cmd = [
            'ffmpeg', '-y', '-i', video_path, '-stream_loop', '-1', '-i', music_path,
            '-filter_complex', f"[1:a]volume={volume}[bg];[0:a][bg]amix=inputs=2:duration=first[aout]",
            '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-shortest', output_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
