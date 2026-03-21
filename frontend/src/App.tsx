import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from './components/Toast';
import Layout from './components/Layout';
import TodaysPicks from './pages/TodaysPicks';
import PlayerProps from './pages/PlayerProps';
import Backtesting from './pages/Backtesting';
import TrackRecord from './pages/TrackRecord';
import PaperTrading from './pages/PaperTrading';
import FAQ from './pages/FAQ';
import Admin from './pages/Admin';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<TodaysPicks />} />
              <Route path="props" element={<PlayerProps />} />
              <Route path="backtesting" element={<Backtesting />} />
              <Route path="track-record" element={<TrackRecord />} />
              <Route path="paper-trading" element={<PaperTrading />} />
              <Route path="faq" element={<FAQ />} />
              <Route path="admin" element={<Admin />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
