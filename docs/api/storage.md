# Storage

Storage layer built on ArcticDB with MinIO (S3-compatible) backend. Includes
cross-process locking and defragmentation utilities.

## ArcticStorage

::: fin3.storage.arctic.ArcticStorage

## Locking

::: fin3.storage.locking.SymbolLock
::: fin3.storage.locking.LockAcquisitionError

## Defragmentation

::: fin3.storage.defrag.SymbolDefragResult
::: fin3.storage.defrag.DefragReport
::: fin3.storage.defrag.get_fragmentation_info
::: fin3.storage.defrag.defragment_library