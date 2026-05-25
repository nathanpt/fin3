### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Compression![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Databento provides options for compressing files from our API. Available
compression formats depend on the [encoding](/docs/api-reference-
historical/basics/encodings) you select.

**Zstd**

The `Zstd` compression option uses the
[Zstandard](http://facebook.github.io/zstd/) format.

This option is available for all encodings, and is recommended for faster
transfer speeds and smaller files.

You can read Zstandard files in Python using the [zstandard
package](https://pypi.org/project/zstandard/).

Read more about [working with Zstandard-compressed files](/docs/standards-and-
conventions/working-with-zstandard).

**None**

The `None` compression option disables compression entirely, resulting in
significantly larger files. However, this can be useful for loading small CSV
files directly into Excel.

