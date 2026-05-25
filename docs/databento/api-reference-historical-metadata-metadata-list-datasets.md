### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical.metadata.list_datasets![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

List all valid [dataset](/docs/api-reference-historical/basics/datasets) IDs
on Databento.

Use this method to list the available dataset IDs (string identifiers), so you
can use other methods which take the `dataset` parameter.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

start_date

optional | date or str

The inclusive UTC start date of the request range as a [Python
date](https://docs.python.org/3/library/datetime.html#date-objects) or [ISO
8601](https://www.iso.org/iso-8601-date-and-time-format.html) date string. If
`None` then first date available.

end_date

optional | date or str

The exclusive UTC end date of the request range as a [Python
date](https://docs.python.org/3/library/datetime.html#date-objects) or [ISO
8601](https://www.iso.org/iso-8601-date-and-time-format.html) date string. If
`None` then last date available.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Returns![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

`list[str]`

A list of available dataset IDs.

