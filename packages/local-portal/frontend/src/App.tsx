import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { Devices } from './pages/Devices'
import { DeviceDetail } from './pages/DeviceDetail'
import { Compliance } from './pages/Compliance'
import { Exports } from './pages/Exports'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/devices/:deviceId" element={<DeviceDetail />} />
        <Route path="/compliance" element={<Compliance />} />
        <Route path="/exports" element={<Exports />} />
      </Routes>
    </Layout>
  )
}

export default App
