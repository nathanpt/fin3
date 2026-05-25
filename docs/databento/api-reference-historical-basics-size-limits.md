### Size
limits

There is no size limit for either stream or batch download requests. Batch
download is more manageable for large datasets, so we recommend using batch
download for requests over 5 GB.

You can also manage the size of your request by splitting it into multiple,
smaller requests. The historical API allows you to make stream and batch
download requests with time ranges specified up to nanosecond resolution. You
can also use the `limit` parameter in any request to limit the number of data
records returned from the service.

[Batch download](/docs/api-reference-historical/batch) supports different
delivery methods which can be specified using the `delivery` parameter.

