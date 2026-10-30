// Webview entry: the same bundle renders either the repository map or the file insights view.
import { createRoot } from 'react-dom/client';
import { FileApp } from './file/FileApp';
import { MapApp } from './map/MapApp';
import './styles.css';

const container = document.getElementById('root')!;
createRoot(container).render(container.dataset.view === 'map' ? <MapApp /> : <FileApp />);
