### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Versioning![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Our historical and live APIs and its client libraries adopt
`MAJOR.MINOR.PATCH` format for version numbers. These version numbers conform
to [semantic versioning](https://semver.org). We are using major version `0`
for initial development, where our API is not considered stable.

Once we release major version `1`, our public API will be stable. This means
that you will be able to upgrade minor or patch versions to pick up new
functionality, without breaking your integration.

Starting with major versions after `1`, we will provide support for previous
versions for one year after the date of the subsequent major release. For
example, if version `2.0.0` is released on January 1, 2024, then all versions
`1.x.y` of the API and client libraries will be deprecated. However, they will
remain supported until January 1, 2025.

We may introduce backwards-compatible changes between minor versions in the
form of:

  * New data [encodings](/docs/api-reference-historical/basics/encodings)
  * Additional fields to existing data [schemas](/docs/api-reference-historical/basics/schemas-and-conventions)
  * Additional batch download [customizations](/docs/api-reference-historical/batch)

Our [Release notes](/docs/release-notes/release-notes-python) will contain
information about both breaking and backwards-compatible changes in each
release.

Our API and official client libraries are kept in sync with same-day releases
for major versions. For instance, `1.x.y` of the C++ client library will use
the same functionality found in any `1.x.y` version of the Python client.

Related: [Release notes](/docs/release-notes/release-notes-python).

