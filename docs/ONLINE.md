# Online Mode

King of Codex includes an experimental socket-based online layer.

## Architecture

- TCP is used for connection setup and reliable lobby/chat style communication.
- UDP is used for gameplay input packets.
- The input layer stores local and remote input history.
- Missing packets are predicted from the most recent known remote input.
- Late packets are compared against predictions.
- If a mismatch is detected within the rollback window, the game shows rollback feedback and can resimulate within the configured limit.

## Ports

```text
TCP: 50007
UDP: 50008
```

Both players may need to allow these ports through their firewall. Internet play may require port forwarding for the host.

## Flow

1. Host selects **Online Mode > Host Match**.
2. Host shares the displayed local IP address.
3. Client selects **Join Match** and enters the host IP.
4. Once connected, both players can chat and enter character select.

## Chat

- `T`: Type a chat message
- `Enter`: Send
- `G`: Quick “GG”

## Caveat

This is a simple built-in implementation intended for testing and LAN-style play. It is not a commercial matchmaking service.
