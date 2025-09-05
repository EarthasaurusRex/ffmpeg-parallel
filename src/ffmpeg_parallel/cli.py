import argparse
import os
from .core import run_ffmpeg_parallel

def main():
    parser = argparse.ArgumentParser(
        description="Encode chunks of a video in parallel using ffmpeg.",
        formatter_class=argparse.RawTextHelpFormatter  # Allows for better formatting in help text
    )
        
    parser.add_argument("video_files", nargs='+', help="The path(s) to the video file(s) to encode.")
    parser.add_argument("-o", "--output", type=str, help="The path for the output file. Defaults to the input directory.")
    parser.add_argument(
        "-c", "--codec", 
        default="libx265", 
        choices=['libx264', 'libx265', 'libsvtav1'], 
        help="The video codec to use for encoding."
    )
    parser.add_argument("-w", "--workers", type=int, default=4, help="The number of parallel workers to run.")
    parser.add_argument("-t", "--threads-per-worker", type=int, default=2, help="The number of threads for each worker process.")
    
    # --- Encoding settings ---
    preset_help = (
        "Controls the encoding speed to compression efficiency trade-off.\n"
        "For libx264/libx265: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow.\n"
        "For libsvtav1: A number from 0-13, where higher is faster."
    )
    parser.add_argument("-p", "--preset", type=str, default=None, help=preset_help)
    parser.add_argument("--crf", type=int, default=23, help="Constant Rate Factor for libx264/libx265 (0-51, lower is better quality).")
    parser.add_argument("--qp", type=int, default=30, help="Quantization Parameter for libsvtav1 (0-63, lower is better quality).")
    parser.add_argument("-m", "--merge", action="store_true", help="Merge all audio tracks from the source file into the output.")

    args = parser.parse_args()

    # --- Set default presets if not provided ---
    preset = args.preset
    if preset is None:
        if args.codec in ['libx264', 'libx265']:
            preset = 'slow'
        elif args.codec == 'libsvtav1':
            preset = '4' # A good balance of speed and quality for SVT-AV1

    # --- Bundle encoding options into a dictionary ---
    encoding_options = {
        'preset': preset,
        'crf': args.crf,
        'qp': args.qp,
        'merge_audio': args.merge
    }

    if len(args.video_files) > 1 and args.output and not os.path.isdir(args.output):
        parser.error("When processing multiple files, the output path must be a directory.")

    for video_file in args.video_files:
        output_path = None
        if args.output:
            if os.path.isdir(args.output):
                base_name = os.path.splitext(os.path.basename(video_file))[0]
                video_ext = '.mp4'
                codec_display_names: dict = {
                    'libx264': 'H.264',
                    'libx265': 'H.265',
                    'libsvtav1': 'AV1',
                }
                codec_suffix: str = codec_display_names.get(args.codec, args.codec)
                output_filename = f"{base_name} [{codec_suffix}]{video_ext}"
                output_path = os.path.join(args.output, output_filename)
            else: # It's a file path, only valid for single video
                output_path = args.output
        
        run_ffmpeg_parallel(
            video_file=video_file,
            output_file=output_path,
            codec=args.codec,
            workers=args.workers,
            threads_per_worker=args.threads_per_worker,
            encoding_options=encoding_options,
        )
