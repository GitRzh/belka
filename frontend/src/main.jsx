import React from 'react'
import ReactDOM from 'react-dom/client'
import { Ion } from 'cesium'
import App from './App.jsx'
import './styles/global.css'

Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
