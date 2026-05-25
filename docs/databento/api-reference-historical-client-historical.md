### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Historical![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

To access Databento's historical API, first create an instance of the
`Historical` client. The entire API is exposed through instance methods of the
client.

Note that the API key can be passed as a parameter, which is [not recommended
for production applications](/docs/portal/api-keys#securing-your-api-keys).
Instead, you can leave out this parameter to pass your API key via the
`DATABENTO_API_KEY` environment variable:

Currently, only `BO1` is supported for historical data.

#### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Parameters![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

key

optional | str

32-character API key. Found on your [API
keys](https://databento.com/portal/keys) page. If `None` then
`DATABENTO_API_KEY` environment variable is used.

gateway

optional | HistoricalGateway or str

Site of historical gateway to connect to. Currently only `BO1` is supported.
If `None` then will connect to the default historical gateway.

