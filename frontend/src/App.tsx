import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import TodaysPicks from './pages/TodaysPicks';
import PlayerProps from './pages/PlayerProps';
import Backtesting from './pages/Backtesting';
import TrackRecord from './pages/TrackRecord';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<TodaysPicks />} />
          <Route path="props" element={<PlayerProps />} />
          <Route path="backtesting" element={<Backtesting />} />
          <Route path="track-record" element={<TrackRecord />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
