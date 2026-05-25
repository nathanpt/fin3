### Metered
pricing

Databento only charges for the data that you use. You can find rates (per MB)
for the various datasets and estimate pricing on our [Data
catalog](https://databento.com/portal/browse). We meter the data by its
uncompressed size in [binary encoding](/docs/standards-and-
conventions/databento-binary-encoding).

When you stream the data, you are billed incrementally for each outbound byte
of data sent from our historical gateway. If your connection is interrupted
while streaming our data and our historical gateway detects connection timeout
over 5 seconds, it will immediately stop sending data and you will not be
billed for the remainder of your request.

Duplicate streaming requests will incur repeated charges. If you intend to
access the same data multiple times, we recommend using our batch download
feature. When you make a batch download request, you are only billed once for
the request and, subsequently, you can download the data from the [Download
center](https://databento.com/portal/download-center) multiple times over 30
days for no additional charge.

You will only be billed for usage of [time series](/docs/api-reference-
historical/timeseries) data. Access to metadata, symbology, and account
management is free. The [Historical.metadata.get_cost](/docs/api-reference-
historical/metadata/metadata-get-cost) method can be used to determine cost
before you request any data.

Related: [Billing management](/docs/portal/billing).

