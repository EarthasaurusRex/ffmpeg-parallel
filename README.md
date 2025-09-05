# ffmpeg-parallel

A tool to split a video and run ffmpeg encodes in parallel.

## Installation

To install the tool, clone the repository and install it using pip:

```bash
git clone https://github.com/EarthasaurusRex/ffmpeg-parallel
cd ffmpeg-parallel
pip install .
```

Alternatively, you can install it directly from the directory if you have it downloaded:

```bash
pip install .
```

## Usage

### Basic Usage

To encode a video with default settings (libx264, 4 workers, 2 threads per worker, medium preset, CRF 23):

```bash
ffmpeg-parallel "path/to/your/video.mp4"
```

To encode multiple videos with default settings:

```bash
ffmpeg-parallel "path/to/your/video.mp4" "path/to/second/video.mp4"
```

The output file will be saved in the same directory as the input video.

### Advanced Usage

You can customize the encoding process with various options:

```bash
ffmpeg-parallel "path/to/your/video.mp4" -o "path/to/output.mkv" -c libx265 --workers 6 --threads-per-worker 4 --preset slow --crf 20
```

This command will:
- Encode `path/to/your/video.mp4`.
- Save the output to `path/to/output.mkv`.
- Use the `libx265` codec.
- Use 6 parallel workers.
- Use 4 threads for each worker.
- Use the `slow` preset for better compression.
- Use a CRF value of 20 for high quality.

### Available Options

- `video_file`: The path to the video file to encode.
- `-o, --output`: The path for the output file. Defaults to the input directory.
- `-c, --codec`: The video codec to use (`libx264`, `libx265`, `libsvtav1`). Default: `libx265`.
- `-w, --workers`: The number of parallel workers. Default: `4`.
- `-t, --threads-per-worker`: The number of threads for each worker. Default: `2`.
- `--preset`: The encoding preset.
  - For `libx264`/`libx265`: `ultrafast`, `superfast`, `veryfast`, `faster`, `fast`, `medium`, `slow`, `slower`, `veryslow`.
  - For `libsvtav1`: A number from 0-13 (higher is faster).
- `--crf`: Constant Rate Factor for `libx264`/`libx265` (0-51, lower is better quality). Default: `23`.
- `--qp`: Quantization Parameter for `libsvtav1` (0-63, lower is better quality). Default: `30`.


## Credits
- Gemini

_This is a personal project. It's designed to run on my computer. The script may not be the most optimal out of the box._