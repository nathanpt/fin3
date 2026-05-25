### ![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)Errors![](/docs/assets/images/link-
icon.8580e188f62b26b8e647.svg)

Our historical API uses HTTP response codes to indicate the success or failure
of an API request. The client library provides exceptions that wrap these
response codes.

  * `2xx` indicates success.
  * `4xx` indicates an error on the client side. Represented as a `BentoClientError`.
  * `5xx` indicates an error with Databento's servers. Represented as a `BentoServerError`.

The full list of the response codes and associated causes is as follows:

Code | Message | Cause  
---|---|---  
200 | OK | Successful request.  
206 | Partial Content | Successful request, with partially resolved symbols.  
400 | Bad Request | Invalid request. Usually due to a missing, malformed or unsupported parameter.  
401 | Unauthorized | Invalid username or API key.  
402 | Payment Required | Issue with your account payment information.  
403 | Forbidden | The API key has insufficient permissions to perform the request.  
404 | Not Found | A resource is not found, or a requested symbol does not exist.  
409 | Conflict | A resource already exists.  
422 | Unprocessable Entity | The request is well formed, but we cannot or will not process the contained instructions.  
429 | Too Many Requests | API rate limit exceeded.  
500 | Internal Server Error | Unexpected condition encountered in our system.  
503 | Service Unavailable | Data gateway is offline or overloaded.  
504 | Gateway Timeout | Data gateway is available but other parts of our system are offline or overloaded.

