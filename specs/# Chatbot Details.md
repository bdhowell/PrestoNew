# Chatbot Details

Like page-specific CSS and JavaScript, each webpage loaded into the iframe can
load `/static/javascript/pages/{page}.does`, using the same route-supplied page
slug. The file is included only if it exists, after the core client and the
page's ordinary JavaScript.

A `.does` file contains executable JavaScript, not JSON, and is served with the
`text/javascript` content type. It registers commands using direct function
references or inline functions, rather than string-valued function names:

```javascript
window.registerWikkitCommands({
    commands: {
        highlight: {
            does: (data) => highlightSection(data),
            data: { section: "introduction" }
        }
    }
});
```

`does` can also be an existing function reference, such as `does: highlightSection`.
The handler receives one argument: an object, an array of objects, or null.
An array is passed intact, not spread into multiple arguments. A command's
`payload` overrides the registered `data`; omitted payload uses that default,
and explicit null is passed through. Registrations belong to the iframe page
and are discarded when that page unloads.

## Server replies

User input is processed by the conversation's `IntentStack`. The server sends
the following protocol; both `say` and `command` are optional:

```json
{
    "say": "Here is your introduction.",
    "command": {
        "does": "highlight",
        "payload": { "section": "introduction" }
    }
}
```

The server sends stable command keys and JSON data, never JavaScript source.
The client accepts legacy `user`/`client` replies and `action` command keys
during migration. The server also normalizes legacy intent-handler results.

An intent handler may return `{"responses": ["First response", "Second response"]}`;
one string is randomly selected as `say` on each dispatch. A command may
accompany those responses.

Page-specific (extrinsic) commands override intrinsic commands with the same
key. Handlers can be async, and commands execute in arrival order. Commands
received before the iframe connects wait for it and its scripts to load.
Unknown commands and execution errors are logged and shown as a chatbot notice.

## Intrinsic commands

- `redirect`: navigate the iframe to the same-origin URL in the string payload.
- `request-response`: start waiting for user input on the client.
  `request` is an alias for the example's original command key.

While a response is requested, the client displays a reminder after 120,000 ms
of inactivity, and again every 120,000 ms. Typing resets the timer. Sending a
nonempty response or closing the socket stops reminders. Rejected overlong
responses leave the server waiting and restart client reminders. There is no
response timeout.

## Server-side chatbot logic

The root `wikkit.py` module owns reply normalization and a `Chatbot` instance
per websocket. The router makes that instance available as
`websocket.state.chatbot`.

- `await chatbot.send(reply)` sends a reply at any time on the socket's event
  loop, without requiring user input.
- `await chatbot.request_response(say=None)` sends the `request-response`
  command, optionally says a prompt, and suspends the calling workflow until
  the next nonempty, length-valid user message arrives. That message is
  returned to the workflow instead of being dispatched as a new intent.
- Override `Chatbot.handle_message` for async conversation workflows that need
  to ask questions and then continue. Ordinary messages continue through the
  existing intent stack.

The receive loop remains active while a workflow waits. Only one response
request may be outstanding per conversation, and disconnecting cancels
pending response waits and the message-processing task.
