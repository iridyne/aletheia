import { forwardRef, useRef, useLayoutEffect } from 'react'

// Shadcn/Radix pattern: measure the inner div's scrollHeight before each
// transition so the browser always has a concrete "from" and "to" pixel value.
// grid-template-rows: 0fr → 1fr can't reliably capture the "from" value at the
// very first frame on close, which produces the visible jump at scrollTop = 0.
const AnimatedSection = forwardRef<HTMLDivElement, { open: boolean; children: React.ReactNode }>(
  ({ open, children }, forwardedRef) => {
    const outerRef = useRef<HTMLDivElement>(null)
    const innerRef = useRef<HTMLDivElement>(null)
    const committedOpen = useRef(open)

    const mergeRef = (el: HTMLDivElement | null) => {
      outerRef.current = el
      if (!forwardedRef) return
      if (typeof forwardedRef === 'function') forwardedRef(el)
      else (forwardedRef as React.MutableRefObject<HTMLDivElement | null>).current = el
    }

    // Set initial collapsed state imperatively so JSX carries no inline styles.
    useLayoutEffect(() => {
      const outer = outerRef.current
      if (!outer) return
      outer.style.overflow = 'hidden'
      outer.style.height = '0px'
      outer.style.opacity = '0'
    }, [])

    useLayoutEffect(() => {
      const outer = outerRef.current
      const inner = innerRef.current
      if (!outer || !inner || open === committedOpen.current) return
      committedOpen.current = open

      if (open) {
        const h = inner.scrollHeight
        outer.style.overflow = 'hidden'
        outer.style.height = '0px'
        outer.style.opacity = '0'
        void outer.offsetHeight                          // force reflow — capture start state
        outer.style.transition = 'height .22s cubic-bezier(.4,0,.2,1), opacity .16s ease-out .06s'
        outer.style.height = `${h}px`
        outer.style.opacity = '1'
        const onEnd = (e: TransitionEvent) => {
          if (e.propertyName !== 'height') return
          outer.style.height = 'auto'
          outer.style.overflow = ''
          outer.style.transition = ''
        }
        outer.addEventListener('transitionend', onEnd)
        return () => outer.removeEventListener('transitionend', onEnd)
      } else {
        const h = outer.offsetHeight                     // snapshot current pixel height
        outer.style.overflow = 'hidden'
        outer.style.height = `${h}px`
        void outer.offsetHeight                          // force reflow — capture start state
        outer.style.transition = 'height .22s cubic-bezier(.4,0,.2,1), opacity .14s ease-out'
        outer.style.height = '0px'
        outer.style.opacity = '0'
        const onEnd = (e: TransitionEvent) => {
          if (e.propertyName !== 'height') return
          outer.style.transition = ''
        }
        outer.addEventListener('transitionend', onEnd)
        return () => outer.removeEventListener('transitionend', onEnd)
      }
    }, [open])

    return (
      <div ref={mergeRef}>
        <div ref={innerRef} className="pt-2">
          {children}
        </div>
      </div>
    )
  }
)
AnimatedSection.displayName = 'AnimatedSection'

export default AnimatedSection
