import subprocess
import os
import math
import hashlib
import shutil
import time
from multiprocessing import Pool, Manager
from tqdm import tqdm

def init_worker(lock):
    """Initializes each worker process with a shared lock for tqdm."""
    tqdm.set_lock(lock)

def encode_chunk(args):
    """Encodes a single video chunk with a dedicated, lock-protected progress bar."""
    chunk_file, encoded_chunks_dir, codec, threads_per_worker, position, encoding_options, estimated_total_frames, progress_queue = args
    
    # --- Prepare for encoding ---
    chunk_name = os.path.basename(chunk_file).split('.')[0]
    output_file = os.path.join(encoded_chunks_dir, os.path.basename(chunk_file))
    progress_log = os.path.join(encoded_chunks_dir, f"{chunk_name}_progress.log")

    # --- Build the ffmpeg command ---
    ffmpeg_encode_command = [
        "ffmpeg", "-i", chunk_file, "-c:v", codec, "-map", "0:v",
        "-threads", str(threads_per_worker), "-y",
    ]
    if codec in ['libx264', 'libx265']:
        ffmpeg_encode_command.extend(["-preset", encoding_options['preset'], "-crf", str(encoding_options['crf'])])
    elif codec == 'libsvtav1':
        ffmpeg_encode_command.extend(["-preset", encoding_options['preset'], "-qp", str(encoding_options['qp'])])
    if encoding_options["merge_audio"]:
        result = subprocess.run(
            ["ffprobe", "-loglevel", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", chunk_file],
             capture_output=True)
        audio_tracks = len(result.stdout.split())
        ffmpeg_encode_command.extend(["-filter_complex", f"amerge=inputs={audio_tracks}", "-c:a", "flac", "-b:a", "320k"])
    else:
        ffmpeg_encode_command.extend(["-map", "0:a", "-c:a", "copy"])
    ffmpeg_encode_command.extend(["-progress", progress_log, output_file])

    # --- Run ffmpeg and monitor progress ---
    process = subprocess.Popen(ffmpeg_encode_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for the log file to be created
    while not os.path.exists(progress_log):
        if process.poll() is not None:
            # print(f"\nWorker {position+1}: ffmpeg process failed to start.")
            return None # Indicate failure
        time.sleep(0.05)

    with tqdm(total=estimated_total_frames, desc=f"Worker {position+1}", position=position, unit="frame", leave=False) as pbar:
        with open(progress_log, 'r') as f:
            current_frame = 0
            while process.poll() is None:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                
                if "frame=" in line:
                    try:
                        frame = int(line.strip().split("=")[1])
                        update_amount = frame - current_frame
                        if update_amount > 0:
                            pbar.update(update_amount)
                            progress_queue.put(update_amount)
                            current_frame = frame
                    except (ValueError, IndexError):
                        continue
        
        # Final update to ensure the bar completes
        if pbar.n < estimated_total_frames:     
            update_amount = estimated_total_frames - pbar.n
            pbar.update(update_amount)
            progress_queue.put(update_amount)

    return output_file


def run_ffmpeg_parallel(video_file, output_file, codec, workers, threads_per_worker, encoding_options):
    """Splits, encodes, and merges a video file in parallel."""
    print(f"Starting parallel encode for {video_file}:")
    print(f"  Codec: {codec}, Workers: {workers}, Threads per worker: {threads_per_worker}")
    print(f"  Encoding options: {encoding_options}")

    # --- Setup temporary directories ---
    
    if output_file is None:
        codec_display_names = {
            'libx264': 'H.264',
            'libx265': 'H.265',
            'libsvtav1': 'AV1',
        }
        codec_suffix = codec_display_names.get(codec, codec) # Fallback to raw codec name if not found

        input_dir = os.path.dirname(os.path.abspath(video_file))
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        video_ext = os.path.splitext(video_file)[1]
        output_file = os.path.join(input_dir, f"{base_name} [{codec_suffix}]{video_ext}")

    temp_dir = f"ffmpeg_parallel_temp {hashlib.md5(str.encode(os.path.split(output_file)[-1])).hexdigest()}"
    chunks_dir = os.path.join(temp_dir, "chunks")
    encoded_chunks_dir = os.path.join(temp_dir, "encoded_chunks")
    for d in [chunks_dir, encoded_chunks_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d)

    # --- Get video duration from original video ---
    ffprobe_info_command = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0",
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", video_file,
    ]
    result = subprocess.run(ffprobe_info_command, capture_output=True, text=True)
    try:
        duration = float(result.stdout)
    except (ValueError, IndexError, ZeroDivisionError) as e:
        print(f"Error: Could not get video duration from {video_file}. Progress bars may not show ETA.")
        print(f"ffprobe stderr: {result.stderr.strip()}")
        print(f"ffprobe stdout: {result.stdout.strip()}")
        duration = 0 # Fallback if ffprobe fails

    # --- Get average frame rate from original video ---
    ffprobe_info_command = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", 
        "-of", "default=noprint_wrappers=1:nokey=1", video_file,
    ]
    result = subprocess.run(ffprobe_info_command, capture_output=True, text=True)
    try:
        avg_frame_rate_str = result.stdout
        # Convert 'num/den' fraction to float
        num, den = map(int, avg_frame_rate_str.split('/'))
        avg_frame_rate = num / den
    except (ValueError, IndexError, ZeroDivisionError) as e:
        print(f"Error: Could not get frame rate from {video_file}. Progress bars may not show ETA.")
        print(f"ffprobe stderr: {result.stderr.strip()}")
        print(f"ffprobe stdout: {result.stdout.strip()}")
        avg_frame_rate = 0.0 # Fallback if ffprobe fails

    # --- Split video into chunks ---
    chunk_duration = duration / workers
    video_ext = os.path.splitext(video_file)[1]
    ffmpeg_split_command = [
        "ffmpeg", "-i", video_file, "-map", "0", "-c", "copy",
    ]
    ffmpeg_split_command.extend([
        "-segment_time", str(chunk_duration), "-f", "segment", "-reset_timestamps", "1",
        os.path.join(chunks_dir, f"chunk_%03d.mkv"),
    ])
    print(f"Splitting video into chunks...")
    subprocess.run(ffmpeg_split_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Splitting complete.")

    # --- Encode chunks in parallel ---
    chunk_files = sorted([os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir)])
    estimated_total_frames = []
    for chunk in chunk_files:
        # --- Get video duration from chunk video ---
        ffprobe_info_command = [
            "ffprobe", "-v", "error", 
            "-select_streams", "v:0",
            "-show_entries", "format=duration", 
            "-of", "default=noprint_wrappers=1:nokey=1", chunk,
        ]
        result = subprocess.run(ffprobe_info_command, capture_output=True, text=True)
        try:
            duration = float(result.stdout)
        except (ValueError, IndexError, ZeroDivisionError) as e:
            print(f"Error: Could not get video duration from {video_file}. Progress bars may not show ETA.")
            print(f"ffprobe stderr: {result.stderr.strip()}")
            print(f"ffprobe stdout: {result.stdout.strip()}")
            duration = 0 # Fallback if ffprobe fails
        estimated_total_frames.append(math.ceil(duration * avg_frame_rate))
    
    manager = Manager()
    lock = manager.Lock()
    tqdm.set_lock(lock)
    progress_queue = manager.Queue()
    total_frames_estimate = sum(estimated_total_frames)

    encode_args = [
        (chunk, encoded_chunks_dir, codec, threads_per_worker, position, encoding_options, estimated_total_frames[position], progress_queue)
        for position, chunk in enumerate(chunk_files)
    ]
    
    print("\nEncoding video chunks in parallel (audio is copied)...\n")
    pool = None # Initialize pool to None
    try:
        with Pool(processes=workers, initializer=init_worker, initargs=(lock,)) as pool, \
             tqdm(total=total_frames_estimate, desc="Overall Progress", unit="frame", position=workers) as overall_pbar:
            
            map_result = pool.map_async(encode_chunk, encode_args)

            while not map_result.ready():
                while not progress_queue.empty():
                    update = progress_queue.get_nowait()
                    overall_pbar.update(update)
                time.sleep(0.1)
            
            # Final queue drain and progress bar update
            while not progress_queue.empty():
                update = progress_queue.get_nowait()
                overall_pbar.update(update)
            
            if overall_pbar.n < total_frames_estimate:
                overall_pbar.update(total_frames_estimate - overall_pbar.n)

            encoded_files = map_result.get()

    except KeyboardInterrupt:
        print("\nEncoding interrupted by user. Cleaning up...")
        if pool:
            pool.terminate()
            pool.join()
        shutil.rmtree(temp_dir)
        return # Exit the function
    
    if None in encoded_files:
        print("\nError: One or more workers failed to encode. Aborting.")
        shutil.rmtree(temp_dir)
        return

    print("\nEncoding complete.")

    # --- Merge encoded chunks ---
    concat_list_path = os.path.join(encoded_chunks_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for file in sorted(encoded_files):
            safe_path = os.path.abspath(file).replace('\\', '/')
            f.write(f"file '{safe_path}'\n")

    ffmpeg_merge_command = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c", "copy", "-map", "0", "-y", output_file,
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