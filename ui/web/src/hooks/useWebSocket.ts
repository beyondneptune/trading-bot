import { useEffect, useRef, useCallback } from 'react'

type WsMessage = Record<string, unknown>
type Handler = (msg: WsMessage) => void

export function useWebSocket(onMessage: Handler) {
  const ws = useRef<WebSocket | null>(null)
  const reconnect = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connect = useCallback(() => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${proto}://${window.location.host}/ws`)

    socket.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        onMessage(data)
      } catch {
        // ignore malformed messages
      }
    }

    socket.onclose = () => {
      reconnect.current = setTimeout(connect, 3000)
    }

    ws.current = socket
  }, [onMessage])

  useEffect(() => {
    connect()
    return () => {
      if (reconnect.current) clearTimeout(reconnect.current)
      ws.current?.close()
    }
  }, [connect])
}
