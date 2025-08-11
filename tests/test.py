stdout = """avg_frame_ratew60/1
durationq10.700000"""

for line in stdout.splitlines():
    key, value = line.split('=')
    print(key, value)

video_info: dict = {key: value for key, value in
                    (line.split('=', 1) for line in stdout.splitlines())}

print(video_info)