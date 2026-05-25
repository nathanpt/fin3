### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Authentication![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Databento uses API keys to authenticate requests. You can view and manage your
keys on the [API keys](https://databento.com/portal/keys) page of your portal.

Each API key is a 32-character string starting with `db-`. By default, our
library uses the environment variable `DATABENTO_API_KEY` as your API key.
However, if you pass an API key to the `Historical` constructor through the
`key` parameter, then this value will be used instead.

Related: [Securing your API keys](/docs/portal/api-keys#securing-your-api-
keys).

