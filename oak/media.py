"""Local title cards, previews and speech. These are not generative image models."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


class ToolUnavailable(RuntimeError):
    pass


class MediaTools:
    def __init__(self, workspace, *, piper_model=None, vosk_model=None, whisper_model=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.output = self.workspace / 'artifacts'
        self.output.mkdir(parents=True, exist_ok=True)
        self.piper_model = piper_model
        self.vosk_model = vosk_model
        self.whisper_model = whisper_model

    def _path(self, suffix):
        return self.output / (uuid.uuid4().hex + suffix)

    def _input(self, value):
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else self.workspace / path).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_file():
            raise ValueError('Input must be an existing file inside the workspace.')
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('Input exceeds the 100 MiB local media limit.')
        return path

    @staticmethod
    def _run(args, *, text=None, timeout=120):
        try:
            result = subprocess.run(args, input=text, capture_output=True, text=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ToolUnavailable('Local media executable is unavailable or timed out.') from None
        if result.returncode:
            # Do not expose command lines or model/runtime diagnostics to chat.
            raise ToolUnavailable('Local media processing failed; inspect the local installation.')
        return result.stdout

    @staticmethod
    def _ffmpeg():
        binary = shutil.which('ffmpeg')
        if not binary:
            raise ToolUnavailable('Install FFmpeg to create video or decode audio.')
        return [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']

    def generate_image(self, title, subtitle='', width=1280, height=720,
                       background='#102d24', accent='#bde96f'):
        """Render a PNG title card/thumbnail with real Unicode typography."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            raise ToolUnavailable('Install Pillow to render local images.') from None
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError('Provide a title of 1–500 characters.')
        if not isinstance(subtitle, str) or len(subtitle) > 500:
            raise ValueError('Subtitle must be at most 500 characters.')
        if any(type(x) is not int or not 256 <= x <= 2048 for x in (width, height)):
            raise ValueError('Image dimensions must be integers between 256 and 2048.')
        font_path = next((p for p in (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
        ) if Path(p).is_file()), None)
        if not font_path:
            raise ToolUnavailable('Install DejaVu Sans fonts for Unicode title cards.')
        image = Image.new('RGB', (width, height), background)
        draw = ImageDraw.Draw(image)
        margin = max(24, width // 14)
        draw.rounded_rectangle((margin, margin, width - margin, height - margin),
                               radius=max(12, height // 30), outline=accent, width=3)
        usable = width - margin * 3

        def wrap(value, font):
            lines, line = [], ''
            for word in value.split():
                for char in word + ' ':
                    candidate = line + char
                    if line and draw.textlength(candidate, font=font) > usable:
                        lines.append(line.rstrip())
                        line = ''
                    line += char
            if line.strip():
                lines.append(line.strip())
            return '\n'.join(lines)

        for size in range(min(96, height // 6), 11, -2):
            font = ImageFont.truetype(font_path, size)
            wrapped = wrap(title, font)
            box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=size // 4)
            if box[3] - box[1] <= height * (0.45 if subtitle else 0.65):
                break
        if box[3] - box[1] > height * (0.45 if subtitle else 0.65):
            raise ValueError('Title is too long for these image dimensions.')
        y = height // 2 - (box[3] - box[1]) // 2 - (height // 12 if subtitle else 0)
        draw.multiline_text((margin * 1.5, y), wrapped, font=font, fill='white', spacing=size // 4)
        if subtitle:
            small = ImageFont.truetype(font_path, max(12, size // 3))
            caption = wrap(subtitle, small)
            caption_box = draw.multiline_textbbox((0, 0), caption, font=small)
            if caption_box[3] - caption_box[1] > height // 5:
                raise ValueError('Subtitle is too long for these image dimensions.')
            draw.multiline_text((margin * 1.5, height * 0.73), caption, font=small, fill=accent)
        output = self._path('.png')
        image.save(output)
        return output

    def generate_video(self, title='', image_path=None, duration=5):
        """Encode a short H.264 title-card preview from a title or local image."""
        return self.montage([image_path or self.generate_image(title)], seconds_per_image=duration)

    def montage(self, image_paths, seconds_per_image=3):
        """Join 1–10 local images into a silent, evenly timed MP4 slideshow."""
        if not isinstance(image_paths, (list, tuple)) or not 1 <= len(image_paths) <= 10:
            raise ValueError('Provide 1–10 workspace images.')
        if isinstance(seconds_per_image, bool) or not isinstance(seconds_per_image, (int, float)) or not 1 <= seconds_per_image <= 10:
            raise ValueError('Each image must last 1–10 seconds.')
        if len(image_paths) * seconds_per_image > 30:
            raise ValueError('Preview duration must not exceed 30 seconds.')
        paths = [self._input(value) for value in image_paths]
        if any(path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'} for path in paths):
            raise ValueError('Use PNG, JPEG or WebP images.')
        output = self._path('.mp4')
        args = self._ffmpeg()
        for path in paths:
            args += ['-loop', '1', '-t', str(seconds_per_image), '-i', str(path)]
        filters = [f'[{i}:v]scale=640:360:force_original_aspect_ratio=decrease,'
                   f'pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{i}]'
                   for i in range(len(paths))]
        filters.append(''.join(f'[v{i}]' for i in range(len(paths))) +
                       f'concat=n={len(paths)}:v=1:a=0[out]')
        self._run(args + ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
                         '-map', '[out]', '-an', '-c:v', 'libx264', '-threads', '2',
                         '-preset', 'veryfast', '-movflags', '+faststart', str(output)])
        return output

    def speak(self, text, language='uk'):
        """Synthesize a WAV using an explicitly configured, local Piper model."""
        if not isinstance(text, str) or not text.strip() or len(text) > 5000:
            raise ValueError('Narration must contain 1–5000 characters.')
        model = Path(self.piper_model).expanduser() if self.piper_model else None
        if model and model.is_file() and Path(str(model) + '.json').is_file():
            metadata = json.loads(Path(str(model) + '.json').read_text())
            code = metadata.get('language', {}).get('code', '')
            if not code.lower().startswith(language.lower().replace('-', '_')):
                raise ValueError('Configured voice model does not match the requested language.')
            if importlib.util.find_spec('piper') is None:
                raise ToolUnavailable('Install piper-tts to use the configured voice model.')
            output = self._path('.wav')
            self._run([sys.executable, '-m', 'piper', '-m', str(model), '-f', str(output)],
                      text=text, timeout=180)
            return output
        if language != 'en':
            raise ToolUnavailable('Configure a local Piper voice model for this language.')
        # FFmpeg flite is deliberately restricted to basic English narration.
        output = self._path('.wav')
        with tempfile.TemporaryDirectory(prefix='oak-speech-') as temp:
            source = Path(temp) / 'speech.txt'
            source.write_text(text)
            self._run(self._ffmpeg() + ['-f', 'lavfi', '-i', f'flite=textfile={source}:voice=slt',
                                       '-ar', '24000', str(output)])
        return output

    def transcribe(self, path, language='uk'):
        """Recognize up to five minutes locally; no downloads or hosted upload."""
        source = self._input(path)
        if self.whisper_model:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise ToolUnavailable('Install faster-whisper for the configured recognizer.') from None
        elif not self.vosk_model or not Path(self.vosk_model).is_dir():
            raise ToolUnavailable('Configure a local Vosk or faster-whisper model.')
        with tempfile.TemporaryDirectory(prefix='oak-transcribe-') as temp:
            decoded = Path(temp) / 'input.wav'
            self._run(self._ffmpeg() + ['-i', str(source), '-t', '301', '-vn', '-ar', '16000',
                                       '-ac', '1', '-c:a', 'pcm_s16le', str(decoded)])
            import wave
            with wave.open(str(decoded), 'rb') as wav:
                if wav.getnframes() / wav.getframerate() > 300:
                    raise ValueError('Voice messages must be no longer than five minutes.')
                if self.whisper_model:
                    model = WhisperModel(str(self.whisper_model), device='cpu', compute_type='int8',
                                         local_files_only=True)
                    segments, _ = model.transcribe(str(decoded), language=language, beam_size=3)
                    result = ' '.join(segment.text.strip() for segment in segments)
                else:
                    try:
                        from vosk import Model, KaldiRecognizer, SetLogLevel
                    except ImportError:
                        raise ToolUnavailable('Install vosk for the configured recognizer.') from None
                    SetLogLevel(-1)
                    recognizer = KaldiRecognizer(Model(str(self.vosk_model)), wav.getframerate())
                    parts = []
                    while chunk := wav.readframes(4000):
                        if recognizer.AcceptWaveform(chunk):
                            parts.append(json.loads(recognizer.Result()).get('text', ''))
                    parts.append(json.loads(recognizer.FinalResult()).get('text', ''))
                    result = ' '.join(parts).strip()
        if not result:
            raise ValueError('No intelligible speech was recognized.')
        return result
