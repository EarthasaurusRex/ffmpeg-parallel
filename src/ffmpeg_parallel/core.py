import subprocess
import os
import math
import hashlib
import shutil
import time
from multiprocessing import Pool, Manager
from tqdm import tqdm

def init_worker(lock) -> None:
    """Initializes each worker process with a shared lock for tqdm."""
    tqdm.set_lock(lock)

def generate_temp_directory(video_file: str) -> tuple[str, str, str]:
    """Creates a temporary directory for encoding a video"""
    file_hash = hashlib.md5(str.encode(os.path.split(video_file)[-1])).hexdigest()
    temp_dir = f"ffmpeg_parallel_temp_{file_hash}"
    chunks_dir = os.path.join(temp_dir, "chunks")
    encoded_chunks_dir = os.path.join(temp_dir, "encoded_chunks")
    for directory in [chunks_dir, encoded_chunks_dir]:
        if os.path.exists(directory):
            shutil.rmtree(directory)
        os.makedirs(directory)
    
    return temp_dir, chunks_dir, encoded_chunks_dir

def get_video_info(video_file: str) -> dict:
    """Gets necessary info about a video file

    Args:
        video_file (str): Path to video file

    Returns:
        tuple[str]: _description_
    """
    ffprobe_info_command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=avg_frame_rate", 
        "-of", "default=noprint_wrappers=1", video_file,
    ]
    result = subprocess.run(ffprobe_info_command, capture_output=True, text=True, check=True)

    video_info: dict = {key: value for key, value in
                        (line.split('=', 1) for line in result.stdout.splitlines())}
    
    return video_info

def chunk_video(video_file: str, chunks_dir: str, chunk_duration: float) -> None:
    """Split video file into chunks

    Args:
        video_file (str): Path to video file
    """
    ffmpeg_split_command = [
        "ffmpeg", "-i", video_file, "-map", "0", "-c", "copy",
        "-segment_time", str(chunk_duration), "-f", "segment", "-reset_timestamps", "1",
        os.path.join(chunks_dir, "chunk_%03d.mkv")
    ]
    print("Splitting video into chunks...")
    subprocess.run(ffmpeg_split_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print("Splitting complete.")

def encode_chunk(args) -> None:
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
             capture_output=True, check=True)
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
        with open(progress_log, 'r', encoding='utf-8') as f:
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

def merge_chunks(chunks: list[str], temp_dir: str, output_file: str) -> None:
    """Merges chunks into a single video

    Args:
        chunks (list[str]): List of chunk files
    """
    concat_list_path = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list_path, 'w', encoding='utf-8') as f:
        for file in sorted(chunks):
            safe_path = os.path.abspath(file).replace('\\', '/')
            f.write(f"file '{safe_path}'\n")

    ffmpeg_merge_command = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c", "copy", "-map", "0", "-y", output_file,
    ]
    print(f"Merging encoded chunks to {output_file}...")
    subprocess.run(ffmpeg_merge_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def print_size_comparison(original_file: str, comparison_file: str) -> None:
    """Prints the size comparison between 2 video files

    Args:
        original_file (str): Path to original file being compared against
        comparison_file (str): Path to file to make the comparison of
    """
    try:
        original_size = os.path.getsize(original_file)
        comparison_size = os.path.getsize(comparison_file)
        if original_size > 0:
            percentage = (comparison_size / original_size) * 100
            print(f"Encoded file size: {comparison_size / (1024*1024):.2f} MiB")
            print(f"Original file size: {original_size / (1024*1024):.2f} MiB")
            print(f"Encoded file is {percentage:.2f}% of the original size.")
        else:
            print("Warning: Original file size is 0, cannot calculate percentage.")
    except FileNotFoundError:
        print("Warning: Could not find original or comparison file for size comparison.")
    except Exception as e:
        print(f"Error calculating file size comparison: {e}")

def run_ffmpeg_parallel(video_file: str, output_file: str, codec: str, workers: int, threads_per_worker: int, encoding_options: dict):
    """Splits, encodes, and merges a video file in parallel."""
    print(f"Starting parallel encode for {video_file}:")
    print(f"  Codec: {codec}, Workers: {workers}, Threads per worker: {threads_per_worker}")
    print(f"  Encoding options: {encoding_options}")

    if output_file is None:
        codec_display_names: dict = {
            'libx264': 'H.264',
            'libx265': 'H.265',
            'libsvtav1': 'AV1',
        }
        codec_suffix: str = codec_display_names.get(codec, codec) # Fallback to raw codec name if not found

        input_dir: str = os.path.dirname(os.path.abspath(video_file))
        base_name: str = os.path.splitext(os.path.basename(video_file))[0]
        video_ext: str = '.mp4' #os.path.splitext(video_file)[1]
        output_file = os.path.join(input_dir, f"{base_name} [{codec_suffix}]{video_ext}")
    
    temp_dir, chunks_dir, encoded_chunks_dir = generate_temp_directory(output_file)

    video_info: dict = get_video_info(video_file)
    duration: float = float(video_info.get('duration', 0))
    numerator: int
    denominator: int
    numerator, denominator = (int(x) for x in video_info.get('avg_frame_rate', '0/0').split('/'))
    avg_frame_rate: float = numerator / denominator

    chunk_video(video_file, chunks_dir, duration/workers)

    chunk_files: list[str] = sorted([os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir)])
    estimated_total_frames: list[int] = []
    for chunk in chunk_files:
        video_info = get_video_info(chunk)
        chunk_duration: float = float(video_info['duration'])
        estimated_total_frames.append(math.ceil(chunk_duration * avg_frame_rate))
    
    manager = Manager()
    lock = manager.Lock()
    tqdm.set_lock(lock)
    progress_queue = manager.Queue()
    total_frames_estimate: int = sum(estimated_total_frames)

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

            encoded_files: list = map_result.get()

    except KeyboardInterrupt:
        print("\nEncoding interrupted by user. Cleaning up...")
        if pool:
            pool.terminate()
            pool.join()
        shutil.rmtree(temp_dir)
        return
    
    if None in encoded_files:
        print("\nError: One or more workers failed to encode. Aborting.")
        shutil.rmtree(temp_dir)
        return

    print("\nEncoding complete.")

    merge_chunks(encoded_files, temp_dir, output_file)

    shutil.rmtree(temp_dir)

    print(f"Successfully created output file: {output_file}")

    print_size_comparison(video_file, output_file)

    print("Done.")