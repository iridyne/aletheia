import { useEffect, useRef, useState } from 'react'

/** Returns a CSS class to apply 380ms price-flash feedback per ui_design_system.md spec. */
export function useCellFlash(value: number | string | null | undefined): 'tick-flash-up' | 'tick-flash-down' | '' {
  const prevRef = useRef(value)
  const [flashClass, setFlashClass] = useState<'' | 'tick-flash-up' | 'tick-flash-down'>('')

  useEffect(() => {
    const prev = Number(prevRef.current)
    const curr = Number(value)

    if (!Number.isNaN(prev) && !Number.isNaN(curr) && prevRef.current !== undefined && prev !== curr) {
      setFlashClass(curr > prev ? 'tick-flash-up' : 'tick-flash-down')
      const timer = setTimeout(() => setFlashClass(''), 380)
      prevRef.current = value
      return () => clearTimeout(timer)
    }
    prevRef.current = value
  }, [value])

  return flashClass
}
