import subprocess
import os
import math
import shutil
import time
from multiprocessing import Pool, Manager
from tqdm import tqdm

def init_worker(lock):
    """Initializes each worker process with a shared lock for tqdm."""
    tqdm.set_lock(lock)

def encode_chunk(args):
    """Encodes a single video chunk with a dedicated, lock-protected progress bar."""
    chunk_file, encoded_chunks_dir, codec, threads_per_worker, position, encoding_options = args
    
    # --- Get total frames for progress bar ---
    ffprobe_command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1", chunk_file
    ]
    result = subprocess.run(ffprobe_command, capture_output=True, text=True)
    try:
        total_frames = int(result.stdout)
    except (ValueError, IndexError):
        print(f"ERROR: {result.stdout}")
        total_frames = 0

    # --- Prepare for encoding ---
    chunk_name = os.path.basename(chunk_file).split('.')[0]
    output_file = os.path.join(encoded_chunks_dir, os.path.basename(chunk_file))
    progress_log = os.path.join(encoded_chunks_dir, f"{chunk_name}_progress.log")

    # --- Build the ffmpeg command ---
    ffmpeg_encode_command = [
        "ffmpeg", "-i", chunk_file, "-c:v", codec, "-c:a", "copy",
        "-threads", str(threads_per_worker), "-y",
    ]
    if codec in ['libx264', 'libx265']:
        ffmpeg_encode_command.extend(["-preset", encoding_options['preset'], "-crf", str(encoding_options['crf'])])
    elif codec == 'libsvtav1':
        ffmpeg_encode_command.extend(["-preset", encoding_options['preset'], "-qp", str(encoding_options['qp'])])
    ffmpeg_encode_command.extend(["-progress", progress_log, output_file])

    # --- Run ffmpeg and monitor progress ---
    process = subprocess.Popen(ffmpeg_encode_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for the log file to be created
    while not os.path.exists(progress_log):
        if process.poll() is not None: # Check if ffmpeg failed to start
            return None # Indicate failure
        time.sleep(0.05)

    with tqdm(total=total_frames, desc=f"Worker {position+1}", position=position, unit="frame", leave=False) as pbar:
        with open(progress_log, 'r') as f:
            current_frame = 0
            while process.poll() is None:
                line = f.readline()
                if not line:
                    time.sleep(0.1) # No new data, wait a bit
                    continue
                
                if "frame=" in line:
                    try:
                        frame = int(line.strip().split("=")[1])
                        update_amount = frame - current_frame
                        if update_amount > 0:
                            pbar.update(update_amount)
                            current_frame = frame
                    except (ValueError, IndexError):
                        continue # Ignore malformed lines
        
        # Final update to ensure the bar completes
        if pbar.n < total_frames:
            pbar.update(total_frames - pbar.n)

    return output_file

def run_ffmpeg_parallel(video_file, output_file, codec, workers, threads_per_worker, encoding_options):
    """Splits, encodes, and merges a video file in parallel."""
    print(f"Starting parallel encode for {video_file}:")
    print(f"  Codec: {codec}, Workers: {workers}, Threads per worker: {threads_per_worker}")
    print(f"  Encoding options: {encoding_options}")

    # --- Setup temporary directories ---
    temp_dir = "ffmpeg_parallel_temp"
    chunks_dir = os.path.join(temp_dir, "chunks")
    encoded_chunks_dir = os.path.join(temp_dir, "encoded_chunks")
    for d in [chunks_dir, encoded_chunks_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d)

    # --- Get video duration ---
    ffprobe_command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_file,
    ]
    result = subprocess.run(ffprobe_command, capture_output=True, text=True)
    try:
        duration = float(result.stdout)
    except (ValueError, IndexError):
        print(f"Error: Could not get video duration from {video_file}.")
        return

    # --- Split video into chunks ---
    chunk_duration = math.ceil(duration / workers)
    video_ext = os.path.splitext(video_file)[1]
    ffmpeg_split_command = [
        "ffmpeg", "-i", video_file, "-c", "copy", "-map", "0",
        "-segment_time", str(chunk_duration), "-f", "segment",
        os.path.join(chunks_dir, f"chunk_%03d{video_ext}"),
    ]
    print(f"Splitting video into chunks...")
    subprocess.run(ffmpeg_split_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Splitting complete.")

    # --- Encode chunks in parallel ---
    chunk_files = sorted([os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir)])
    manager = Manager()
    lock = manager.Lock()
    encode_args = [(chunk, encoded_chunks_dir, codec, threads_per_worker, i, encoding_options) for i, chunk in enumerate(chunk_files)]
    
    print("\nEncoding video chunks in parallel (audio is copied)...\n")
    with Pool(processes=workers, initializer=init_worker, initargs=(lock,)) as pool:
        encoded_files = pool.map(encode_chunk, encode_args)
    
    if None in encoded_files:
        print("\nError: One or more workers failed to encode. Aborting.")
        shutil.rmtree(temp_dir)
        return

    print("\nEncoding complete.")

    # --- Merge encoded chunks ---
    if output_file is None:
        codec_display_names = {
            'libx264': 'H.264',
            'libx265': 'H.265',
            'libsvtav1': 'AV1',
        }
        codec_suffix = codec_display_names.get(codec, codec) # Fallback to raw codec name if not found

        input_dir = os.path.dirname(os.path.abspath(video_file))
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        output_file = os.path.join(input_dir, f"{base_name} [{codec_suffix}]{video_ext}")

    concat_list_path = os.path.join(encoded_chunks_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for file in sorted(encoded_files):
            safe_path = os.path.abspath(file).replace('\\', '/')
            f.write(f"file '{safe_path}'\n")

    ffmpeg_merge_command = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c", "copy", "-y", output_file,
    ]
    print(f"Merging encoded chunks to {output_file}...")
    subprocess.run(ffmpeg_merge_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # --- Clean up ---
    shutil.rmtree(temp_dir)
    print(f"Successfully created output file: {output_file}")

    # --- Print size comparison ---
    try:
        original_size = os.path.getsize(video_file)
        encoded_size = os.path.getsize(output_file)
        if original_size > 0:
            percentage = (encoded_size / original_size) * 100
            print(f"Encoded file size: {encoded_size / (1024*1024):.2f} MB")
            print(f"Original file size: {original_size / (1024*1024):.2f} MB")
            print(f"Encoded file is {percentage:.2f}% of the original size.")
        else:
            print("Warning: Original file size is 0, cannot calculate percentage.")
    except FileNotFoundError:
        print("Warning: Could not find original or encoded file for size comparison.")
    except Exception as e:
        print(f"Error calculating file size comparison: {e}")

    print("Done.")
