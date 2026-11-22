// Simple typed event emitter for auth:logout and similar events
// Replaces window.dispatchEvent / window.addEventListener in React Native

type Listener = (...args: any[]) => void

class EventEmitter {
  private listeners: Map<string, Set<Listener>> = new Map()

  on(event: string, listener: Listener): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set())
    }
    this.listeners.get(event)!.add(listener)
    return () => this.off(event, listener)
  }

  off(event: string, listener: Listener): void {
    this.listeners.get(event)?.delete(listener)
  }

  emit(event: string, ...args: any[]): void {
    this.listeners.get(event)?.forEach(fn => fn(...args))
  }

  removeAll(): void {
    this.listeners.clear()
  }
}

export const appEvents = new EventEmitter()
