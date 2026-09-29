// Owner key: remembered in this browser (localStorage). A player's PIN:
// remembered for this tab only (sessionStorage). Storage can throw (private
// mode, blocked site data), so every access is guarded.
const OWNER = 'sp.ownerKey'
const pinKey = (userId: number) => `sp.pin.${userId}`

export function getOwnerKey(): string | null {
  try { return localStorage.getItem(OWNER) } catch { return null }
}

export function setOwnerKey(key: string | null): void {
  try { if (key) localStorage.setItem(OWNER, key); else localStorage.removeItem(OWNER) } catch { /* unavailable */ }
}

export function getPin(userId: number): string | null {
  try { return sessionStorage.getItem(pinKey(userId)) } catch { return null }
}

export function setPin(userId: number, pin: string | null): void {
  try { if (pin) sessionStorage.setItem(pinKey(userId), pin); else sessionStorage.removeItem(pinKey(userId)) } catch { /* unavailable */ }
}
