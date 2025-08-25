import multiprocessing

# Controls the number of parallel processes.
# A good starting point is half your CPU cores to avoid memory exhaustion,
# especially with OCR. Adjust this based on system RAM and performance.
# e.g. For an 8-core machine, start with 4.
WORKERS = max(1, multiprocessing.cpu_count() // 2)

# The maximum time in seconds a single PDF is allowed to take.
# If a file processing takes longer, the job is cancelled and marked as a failure.
# This prevents a single corrupt or complex file from stalling the entire pipeline.
JOB_TIMEOUT_SECONDS = 120  # 2 minutes

# If the average number of text characters on the first few pages is below this,
# the file is classified as needing OCR.
TEXT_CHARACTER_THRESHOLD = 50

# To speed up the check, only analyze the first N pages of a document.
PAGES_TO_CHECK_FOR_OCR = 10

# The resolution (Dots Per Inch) for rendering PDF pages to images before OCR.
# Lower DPI is MUCH faster and uses significantly less memory.
# - 150: Fastest, lowest quality. Good for clean documents.
# - 200: A great balance of speed and quality. (Recommended)
# - 300: Slower, higher quality. Use for documents with small or unclear text.
OCR_DPI = 200
