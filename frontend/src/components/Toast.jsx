import React from 'react'

export default function Toast({ message, type = 'success' }) {
  const isError = type === 'error'
  return (
    <div
      className={`
        fixed bottom-6 left-1/2 -translate-x-1/2 z-50
        flex items-center gap-2.5
        px-4 py-3 rounded-xl shadow-lg
        text-sm font-medium
        animate-slide-up
        ${isError
          ? 'bg-red-600 text-white'
          : 'bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900'
        }
      `}
      role="status"
    >
      {isError ? (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
      ) : (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      )}
      {message}
    </div>
  )
}
