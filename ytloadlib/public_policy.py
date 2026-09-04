from __future__ import annotations

PUBLIC_FILE_BYTES = 5 * 1024 ** 3
PUBLIC_SESSION_BYTES = 12 * 1024 ** 3
PUBLIC_TOTAL_BYTES = 20 * 1024 ** 3
PUBLIC_JOB_MAX_SECONDS = 3 * 60 * 60
PUBLIC_RESULT_TTL = 60 * 60
PUBLIC_SESSION_TTL = 60 * 60
PUBLIC_START_FREE_BYTES = 12 * 1024 ** 3
PUBLIC_CRITICAL_FREE_BYTES = 4 * 1024 ** 3
PUBLIC_SESSION_QUEUE = 4
PUBLIC_GLOBAL_QUEUE = 12
PUBLIC_STREAM_TIMEOUT = 5 * 60
PUBLIC_COLLECTION_ITEMS = 50
PUBLIC_FILE_LIMIT_ERROR = 'The processed file exceeds the 5 GB public web limit. Choose a lower quality or use YTLoad Local.'
PUBLIC_EXPIRED_ERROR = 'Temporary file expired. Run the download again.'
PUBLIC_STORAGE_ERROR = 'Temporary storage is full. Save your files and try again later, or use YTLoad Local.'
