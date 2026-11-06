# little talk

Turn a JSON or YAML conversation into an iMessage-style video. Preview the chat in your browser, then export it as an MP4. Everything runs locally on your computer.

## Get started

You’ll need **Python 3.10 or newer** and **Node.js 20 or newer** (npm is included with Node.js).

1. Open a terminal in this project folder.
2. Create and activate a Python virtual environment:

   **macOS / Linux**

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   **Windows PowerShell**

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. Install the app’s dependencies and build the browser preview:

   **macOS / Linux**

   ```sh
   python3 -m pip install -r requirements.txt
   npm ci
   npm run build:preview
   ```

   **Windows**

   ```powershell
   py -m pip install -r requirements.txt
   npm ci
   npm run build:preview
   ```

4. Start the app:

   ```sh
   python server.py
   ```

   On macOS or Linux, you can also use `python3 server.py`.

5. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

The first MP4 export may take a little longer while Remotion prepares its renderer. Keep the terminal open while using the app; press **Ctrl+C** there to stop it.

## Make a conversation

Paste a script into the editor, or import a `.json` or `.yaml` file. The examples folder has ready-to-try [JSON](./examples/conversation.json) and [YAML](./examples/conversation.yaml) scripts.

```json
{
  "contact": "Maya Chen",
  "start_time": "21:14",
  "messages": [
    {
      "sender": "Maya",
      "reply_delay": 0.8,
      "typing_duration": 1.5,
      "text": "Hey, are we still on for tonight?"
    },
    {
      "sender": "you",
      "reply_delay": 1.2,
      "typing_duration": 1.8,
      "text": "Absolutely. See you soon!"
    }
  ]
}
```

### Script fields

- `contact` (optional): Name shown in the chat header. If omitted, the first incoming sender is used.
- `avatar` (optional): Custom text or emoji for the contact icon. Otherwise, the app uses the contact’s initials, or 👻 if there’s no contact name.
- `start_time` (optional): Starting 24-hour clock time (`HH:MM`); defaults to `09:41`.
- `history` (optional): Up to 20 earlier messages shown before the animated conversation. Each has a `sender`, `text`, and optional `timestamp`.
- `messages` (required): Up to 50 animated messages. Each has a `sender`, `text` (or `message`), and optional timing fields:
  - `reply_delay`: Seconds before the sender starts composing; defaults to `0.9` (range `0`–`30`). `delay_before_typing` is also accepted.
  - `typing_duration`: Seconds spent composing; defaults to `1.2` (range `0`–`30`). `typing` is also accepted.
  - `timestamp`: Optional 24-hour `HH:MM` time for the message.

Set `sender` to `you`, `user`, or `me` for an outgoing message. Other sender names appear as incoming messages. Incoming messages show typing dots; outgoing messages are typed in the composer before sending. Message text can be up to 1,000 characters, and videos are limited to 180 seconds.

The chat clock follows message timestamps. A centered time marker appears when messages are at least an hour apart. Earlier messages and time markers scroll with the conversation.

## Export options

- **Preview box · no caption**: Exports the whole preview card and phone at 1080 × 1620.
- **Match phone**: Exports the phone-shaped scene at 1170 × 2532.
- **Portrait** or **Square**: Exports at 1080 × 1920 or 1080 × 1080.
- Choose 18, 24, or 30 frames per second.

The browser preview and MP4 export use the same Remotion composition. If you edit the scene under `src/`, rebuild the browser preview with `npm run build:preview`; MP4 exports bundle the scene automatically.

## Troubleshooting

- If `npm` is not recognized, install Node.js 20+ and reopen your terminal.
- If Python dependencies are missing, activate the virtual environment and rerun the `pip install` command above.
- If the preview bundle is missing, run `npm run build:preview` from the project folder.
- To use another port, start the app with `python server.py --port 8001` (replace `8001` with your preferred port).

## Run the tests

With the virtual environment active:

```sh
python -m unittest
```
