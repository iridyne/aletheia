/**
 * Framework-Agnostic Storage Service for Aletheia
 * 
 * Provides type-safe access to localStorage with automatic JSON serialization,
 * error boundaries, and zero UI-framework dependencies (React/Solid-neutral).
 */

export interface StorageSchema {
  // Sidebar rail state
  'aletheia.sidebar.rail.collapsed': boolean
  // Remote control authorization token
  'aletheia.remoteControlToken': string
  // Agent feed view mode
  'aletheia.feedMode': 'narrative' | 'ops' | 'both'
  // Prediction markets observer enabled cache
  'aletheia.prediction.enabled': boolean
  // Charts settings JSON
  'aletheia.charts.settings.v1': unknown
  // Prediction markets observer enabled cache
  'aletheia.prediction.enabled.cache': boolean
  // Table column visibility maps
  [key: `dt-cols:${string}`]: Record<string, boolean>
}

export class StorageService {
  /**
   * Reads a value from storage with JSON parsing.
   * Returns fallback if key is not found, storage is unavailable, or parsing fails.
   */
  static get<K extends keyof StorageSchema>(key: K, fallback: StorageSchema[K]): StorageSchema[K] {
    if (typeof window === 'undefined' || !window.localStorage) return fallback
    try {
      const raw = window.localStorage.getItem(key as string)
      if (raw === null) return fallback
      try {
        return JSON.parse(raw) as StorageSchema[K]
      } catch {
        return raw as unknown as StorageSchema[K]
      }
    } catch {
      return fallback
    }
  }

  /**
   * Reads a raw string directly without JSON decoding.
   */
  static getString(key: string, fallback = ''): string {
    if (typeof window === 'undefined' || !window.localStorage) return fallback
    try {
      const raw = window.localStorage.getItem(key)
      return raw !== null ? raw : fallback
    } catch {
      return fallback
    }
  }

  /**
   * Writes a value to storage with JSON serialization.
   */
  static set<K extends keyof StorageSchema>(key: K, value: StorageSchema[K]): void {
    if (typeof window === 'undefined' || !window.localStorage) return
    try {
      const serialized = typeof value === 'string' ? value : JSON.stringify(value)
      window.localStorage.setItem(key as string, serialized)
    } catch (e) {
      console.warn(`[StorageService] Failed to set item for key ${String(key)}:`, e)
    }
  }

  /**
   * Sets a raw string directly.
   * Re-throws storage exceptions so callers who need to report failures can do so.
   */
  static setString(key: string, value: string): void {
    if (typeof window === 'undefined' || !window.localStorage) {
      throw new Error('Browser storage is unavailable')
    }
    window.localStorage.setItem(key, value)
  }

  /**
   * Removes an item from storage.
   */
  static remove(key: string): void {
    if (typeof window === 'undefined' || !window.localStorage) return
    try {
      window.localStorage.removeItem(key)
    } catch {
      /* ignore storage deletion failures */
    }
  }
}
