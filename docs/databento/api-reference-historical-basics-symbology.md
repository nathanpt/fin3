### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Symbology![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Databento's historical API supports several ways to select an instrument in a
[dataset](/docs/api-reference-historical/basics/datasets). An instrument is
specified using a **symbol** and a **symbology type** , also referred to as an
**stype**. The supported symbology types are:

  * **[Raw symbology](/docs/standards-and-conventions/symbology#raw-symbol)** (`raw_symbol`) original string symbols used by the publisher in the source data.
  * **[Instrument ID symbology](/docs/standards-and-conventions/symbology#instrument-id)** (`instrument_id`) unique numeric ID assigned to each instrument by the publisher.
  * **[Parent symbology](/docs/standards-and-conventions/symbology#parent)** (`parent`) groups instruments related to the market for the same underlying.
  * **[Continuous contract symbology](/docs/standards-and-conventions/symbology#continuous)** (`continuous`) proprietary symbology that specifies instruments based on certain systematic rules.

When requesting data from our [timeseries.get_range](/docs/api-reference-
historical/timeseries/timeseries-get-range) or [batch.submit_job](/docs/api-
reference-historical/batch/batch-submit-job) endpoints, an input and output
symbology type can be specified. By default, our client libraries will use raw
symbology for the input type and instrument ID symbology for the output type.
Not all symbology types are supported for every dataset.

The process of converting between one symbology type to another is called
**symbology resolution**. This conversion can be done, for no cost, with the
[symbology.resolve](/docs/api-reference-historical/symbology/symbology-
resolve) endpoint.

For more about symbology at Databento, see our [Standards and
conventions](/docs/standards-and-conventions/symbology).

