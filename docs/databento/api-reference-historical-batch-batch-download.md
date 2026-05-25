### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical.batch.download![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Download a batch job or a specific file to `{output_dir}/{job_id}/`.

Will automatically generate any necessary directories if they do not already
exist.

Related: [Download center](https://databento.com/portal/download-center).

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

job_id

required | str

The batch job identifier.

output_dir

optional | PathLike[str] or str

The directory to download the file(s) to. If `None`, defaults to the current
working directory.

filename_to_download

optional | str

The specific file to download. If `None` then will download all files for the
batch job.

keep_zip

optional | bool, default False

If `True`, and `filename_to_download` is `None`, all job files will be saved
as a .zip archive in the `output_dir`.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

`list[Path]`

A list of paths to the downloaded files.

