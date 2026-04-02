import React, { useState, useEffect, useCallback } from 'react'
import Form from './components/Form.jsx'
import Output from './components/Output.jsx'
import Toast from './components/Toast.jsx'

// ---------------------------------------------------------------------------
// Mock CSV — shown when VITE_MOCK=true or when the API is unreachable in dev
// ---------------------------------------------------------------------------
const MOCK_CSV = `"Topic","Slide","Heading","Description"
"Daily Journaling","1","Transform Your Life Daily","Start journaling today and unlock clarity, creativity, and personal growth you never imagined possible."
"Daily Journaling","2","Reduce Stress Instantly","Writing your thoughts daily lowers cortisol levels and helps you process emotions before they overwhelm you."
"Daily Journaling","3","Boost Your Creativity","Journaling sparks new ideas by connecting thoughts freely, giving your creative mind space to breathe and explore."
"Daily Journaling","4","Track Real Progress","Reviewing past entries reveals patterns, celebrates wins, and shows how far you have grown over time."
"Daily Journaling","5","Start Your Journey Today","Grab a notebook, write three sentences daily. Your future self will thank you. Begin tonight."`

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [dark, setDark]     = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const [status, setStatus] = useState('idle')   // idle | loading | done | error
  const [csv, setCsv]       = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [toast, setToast]   = useState(null)      // { message, type }

  // Sync dark class on <html>
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3500)
  }, [])

  const handleGenerate = useCallback(async ({ topic, tone, slides }) => {
    setStatus('loading')
    setCsv('')
    setErrorMsg('')

    // Build the full prompt string — the backend takes a plain topic string
    let fullTopic = topic.trim()
    if (slides !== 5)               fullTopic += `. Create exactly ${slides} slides.`
    if (tone !== 'professional')    fullTopic += ` Use a ${tone} tone.`

    // Mock mode (set VITE_MOCK=true in .env.local to bypass the API)
    if (import.meta.env.VITE_MOCK === 'true') {
      await new Promise(r => setTimeout(r, 1800))
      setCsv(MOCK_CSV)
      setStatus('done')
      showToast('Carousel generated! (demo mode)')
      return
    }

    try {
      const res = await fetch('/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: fullTopic }),
      })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }

      const data = await res.json()
      setCsv(data.csv)
      setStatus('done')
      showToast('Carousel generated!')
    } catch (err) {
      const message = err.message?.includes('Failed to fetch')
        ? 'Could not reach the server. Check your connection.'
        : err.message || 'Something went wrong.'
      setErrorMsg(message)
      setStatus('error')
      showToast(message, 'error')
    }
  }, [showToast])

  const handleReset = useCallback(() => {
    setStatus('idle')
    setCsv('')
    setErrorMsg('')
  }, [])

  return (
    <div className="min-h-screen flex flex-col">
      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950">
        <div className="max-w-2xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="w-6 h-6 rounded-md bg-accent flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <rect x="1" y="1" width="5" height="5" rx="1" fill="white"/>
                <rect x="8" y="1" width="5" height="5" rx="1" fill="white" fillOpacity=".6"/>
                <rect x="1" y="8" width="5" height="5" rx="1" fill="white" fillOpacity=".6"/>
                <rect x="8" y="8" width="5" height="5" rx="1" fill="white" fillOpacity=".3"/>
              </svg>
            </span>
            <span className="font-semibold text-sm tracking-tight">Carousel</span>
          </div>

          <button
            onClick={() => setDark(d => !d)}
            aria-label="Toggle dark mode"
            className="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:text-gray-900 hover:bg-gray-100 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            {dark ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/>
                <line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/>
                <line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
              </svg>
            )}
          </button>
        </div>
      </header>

      {/* ── Main ───────────────────────────────────────────────── */}
      <main className="flex-1 max-w-2xl mx-auto w-full px-4 py-12 flex flex-col gap-10">

        {/* Hero */}
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">
            Generate your carousel
          </h1>
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            Turn any topic into a polished 5-slide Instagram carousel in seconds.
          </p>
        </div>

        {/* Form */}
        <Form
          onGenerate={handleGenerate}
          loading={status === 'loading'}
          onReset={status !== 'idle' ? handleReset : null}
        />

        {/* Output */}
        {(status === 'loading' || status === 'done' || status === 'error') && (
          <Output
            status={status}
            csv={csv}
            errorMsg={errorMsg}
            onToast={showToast}
          />
        )}
      </main>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <footer className="text-center text-xs text-gray-400 dark:text-gray-600 py-6">
        Powered by Claude · Contentdrips
      </footer>

      {/* ── Toast ──────────────────────────────────────────────── */}
      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  )
}
