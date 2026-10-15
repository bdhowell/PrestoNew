We're going to build user intent-handling logic in intents.py

At any given time there is a stack of maps of intents and their examples (examples stack), and a stack of maps of intents and their handler functions (handlers stack).
Handler functions may be null.

At the bottom of the examples stack is a map of general intents and examples: 

## General Intents: 
- sign-out: sign out, sign out, log off, logout
- sign-up: register, create an account, sign up, sign-up, make a poster
- sign-in: sign in, log in, login, log-in
- product-pricing: pricing, what does it cost, is this free
- reset-password: reset password, change password, lost password
- purchase: buy
- delete: remove, delete, discard
- create-poster: create poster
- delete-poster: delete poster
- download: downloads
- help: help
- undo: undo
- redo: redo
- okay: okay

At the bottom of the handlers stack is a map of general intents and handler functions: (handler functions can be null)

## General Intents Map:
- sign-up: lambda redirect to `/account/register`
- sign-in: lambda redirect to `/acccount/signin`
- sign-out: lambda redirect to `/account/signout`
- product-pricing: lambda redirect to `/wikkit/pricing`
- reset-password: lambda redirect to `/account/reset`
- help: lambda redirect to `/wikkit/help`
- create-poster: lambda redirect to /poster/create
- purchase: None
- delete: None
- create-poster: None
- delete-poster: None
- download: None
- help: None
- undo: None
- redo: None
- okay: None

Additional examples maps and handlers maps can be pushed onto and popped from their respective stacks in pairs.
Handler maps can also be pushed individually.
It is an error for a handlers map to not have an intent which is found in any examples map currently on the examples stack.

There also need to be a function which allows either or both stacks to be popped.
Popping always pops the last handlers map and the examples map with which it was pushed, if any.
It is not possible to pop the general examples and handlers map.

To classify and act upon a user's intent:
Accumulate all intents and examples from the examples stack, starting with general at bottom and continuing with any pushed interests and examples in order.
When two or more sets of intents and examples contain the same intent, use the examples from the highest level set of intents and examples.

Use the logic in intents.identify_intents as a model for classifying the user's intent.

To handle an intent, start with the top of handlers stack.
- If there is no handler for the intent then continue with the next level down of intents and handlers.
	- If there is no next set of intents and handlers then raise an exception.
- If there is an intent handler in the set and its value is None then return None.
- If there is a handler for an intent then invoke the handler function for the intent, passing any entities found and the user's message to the handler as parameters. Return the intent, extracted entities, and handler result together. Otherwise, pass the message back to the client with intent information as a JSON data structure.
