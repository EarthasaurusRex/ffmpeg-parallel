import argparse
from .core import run_ffmpeg_parallel

def main():
    parser = argparse.ArgumentParser(
        description="Encode chunks of a video in parallel using ffmpeg.",
        formatter_class=argparse.RawTextHelpFormatter  # Allows for better formatting in help text
    )
        
    parser.add_argument("video_file", help="The path to the video file to encode.")
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
    parser.add_argument("--preset", type=str, default=None, help=preset_help)
    parser.add_argument("--crf", type=int, default=23, help="Constant Rate Factor for libx264/libx265 (0-51, lower is better quality).")
    parser.add_argument("--qp", type=int, default=30, help="Quantization Parameter for libsvtav1 (0-63, lower is better quality).")

    args = parser.parse_args()

    # --- Set default presets if not provided ---
    preset = args.preset
    if preset is None:
        if args.codec in ['libx264', 'libx265']:
            preset = 'medium'
        elif args.codec == 'libsvtav1':
            preset = '7' # A good balance of speed and quality for SVT-AV1

    # --- Bundle encoding options into a dictionary ---
    encoding_options = {
        'preset': preset,
        'crf': args.crf,
        'qp': args.qp,
    }

    run_ffmpeg_parallel(
        video_file=args.video_file, 
        output_file=args.output,
        codec=args.codec, 
        workers=args.workers, 
        threads_per_worker=args.threads_per_worker,
        encoding_options=encoding_options
    )
