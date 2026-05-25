### Rate
limits

Our historical API allows each IP address up to:

  * 100 concurrent connections.
  * 100 [time series](/docs/api-reference-historical/timeseries) requests per second.
  * 100 [symbology](/docs/api-reference-historical/symbology) requests per second.
  * 20 [metadata](/docs/api-reference-historical/metadata) requests per second.
  * 20 [batch list jobs](/docs/api-reference-historical/batch/batch-list-jobs) requests per second.
  * 20 [batch submit job](/docs/api-reference-historical/batch/batch-submit-job) requests per minute.

When a request exceeds a rate limit, a `BentoClientError` exception is raised
with a 429 error code.

**Retry-After**

The Retry-After response header indicates how long the user should wait before
retrying.

If you find that your application has been rate-limited, you can retry after
waiting for the time specified in the Retry-After header.

If you are using Python, you may use the time.sleep function as seen below to
wait for the time specified in the Retry-After header. e.g.
`time.sleep(int(response.headers("Retry-After", 1)))`

This code snippet works best for our current APIs with their rate limits.
Future APIs may have different rate limits, and might require a different
default time delay.

