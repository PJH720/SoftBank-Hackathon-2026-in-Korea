import { useEffect, useState } from 'react'
import App from './App.jsx'
import Shell from './Shell.jsx'

// "#/one-touch/..." opens the one-touch screens (Shell); everything else is the 3D dashboard (App).
const oneTouch = () => window.location.hash.startsWith('#/one-touch')

export default function Root() {
  const [route, setRoute] = useState(oneTouch)
  useEffect(() => {
    const onHash = () => setRoute(oneTouch())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return route ? <Shell /> : <App />
}
