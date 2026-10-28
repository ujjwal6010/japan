import React, { useState, useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Polyline, Tooltip, Polygon, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import * as turf from '@turf/turf';
import { Activity, Package, Truck, Zap, BrainCircuit, X, Droplets, Utensils, CupSoda, CloudLightning, Trophy, Flame, RotateCcw } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer } from 'recharts';
import './index.css';

const createIcon = (className) => L.divIcon({ className, iconSize: [20, 20], iconAnchor: [10, 10] });
const healthyIcon = createIcon('store-marker-healthy');
const sinkIcon = createIcon('store-marker-sink');
const sourceIcon = createIcon('store-marker-source');
const driverIcon = L.divIcon({ className: 'driver-marker', iconSize: [16, 16], iconAnchor: [8, 8] });

const BANGALORE_CENTER = [12.9716, 77.5946];

const poissonPmf = (k, lambda) => {
  let fact = 1;
  for(let i=1; i<=k; i++) fact *= i;
  return (Math.pow(lambda, k) * Math.exp(-lambda)) / fact;
};

const ProductIcon = ({ name }) => {
  if (name === "Daily Essentials") return <Droplets size={14} color="#3b82f6" />;
  if (name === "Match Day Snacks") return <Utensils size={14} color="#f59e0b" />;
  if (name === "Cold Beverages") return <CupSoda size={14} color="#10b981" />;
  return <Package size={14} />;
};

function MapRecenter({ coords }) {
  const map = useMap();
  useEffect(() => {
    if (coords && coords.length > 0) {
      map.setView(BANGALORE_CENTER, 12, { animate: true });
      setTimeout(() => {
        map.invalidateSize();
      }, 200);
    }
  }, [coords, map]);
  return null;
}

function App() {
  const [gameState, setGameState] = useState({ stores: {}, drivers: [], routes: [], ghost_routes: [], insights: [], current_scenario: "NORMAL", active_spikes: [] });
  const [isConnected, setIsConnected] = useState(false);
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState("Match Day Snacks");
  const [voronoiPolygons, setVoronoiPolygons] = useState([]);
  const ws = useRef(null);

  const chartData = React.useMemo(() => {
    if (!selectedNode || !gameState.stores[selectedNode]) return [];
    const prod = gameState.stores[selectedNode].products[selectedProduct];
    if (!prod) return [];
    const data = [];
    for (let k = 0; k <= Math.max(30, prod.lambda * 2); k++) {
      data.push({ k: k, probability: (poissonPmf(k, prod.lambda) * 100).toFixed(2) });
    }
    return data;
  }, [selectedNode, selectedProduct, gameState.stores]);

  useEffect(() => {
    ws.current = new WebSocket('wss://zepto-mesh-backend.onrender.com/ws');
    ws.current.onopen = () => setIsConnected(true);
    ws.current.onclose = () => setIsConnected(false);
    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setGameState(data);

      if (data.stores && Object.keys(data.stores).length > 0) {
        const storeEntries = Object.entries(data.stores);
        
        const processedNodes = storeEntries.map(([name, info]) => {
          if (!info || !info.coords) return null;
          return {
            id: name,
            name: name.replace('Store_', '').replace('_', ' '),
            pos: info.coords,
            stockout_prob: info.stockout_prob ?? 0,
            status: info.status ?? 'HEALTHY',
            products: info.products ?? {}
          };
        }).filter(Boolean);

        if (processedNodes.length > 2) {
          const points = turf.featureCollection(
            processedNodes.map(n => 
              turf.point([n.pos[1], n.pos[0]], { id: n.id, status: n.status, prob: n.stockout_prob })
            )
          );

          const bbox = [77.4000, 12.8000, 77.9000, 13.2000]; 
          const voronoiPolys = turf.voronoi(points, { bbox: bbox });

          if (voronoiPolys && voronoiPolys.features) {
            const mappedPolys = voronoiPolys.features.map((feature, index) => {
              if (!feature || !feature.geometry) return null;
              const leafletCoords = feature.geometry.coordinates[0].map(coord => [coord[1], coord[0]]);
              const associatedPoint = points.features[index]?.properties || { id: 'unknown', status: 'HEALTHY', prob: 0 };

              return {
                id: associatedPoint.id,
                coords: leafletCoords,
                prob: associatedPoint.prob,
                status: associatedPoint.status
              };
            }).filter(Boolean);

            setVoronoiPolygons(mappedPolys);
          }
        }
      }
    };
    return () => ws.current && ws.current.close();
  }, []);

  const triggerScenario = (scenarioName) => {
    if (ws.current && isConnected) {
      ws.current.send(JSON.stringify({ action: "SET_SCENARIO", value: scenarioName }));
    }
  };

  const toggleManualSpike = (storeId) => {
    if (ws.current && isConnected) {
      ws.current.send(JSON.stringify({ action: "TOGGLE_SPIKE", value: storeId }));
    }
  };

  const resetSimulation = () => {
    if (ws.current && isConnected) {
      ws.current.send(JSON.stringify({ action: "RESET" }));
    }
  };

  const getIconForStatus = (status) => {
    if (status === 'SINK') return sinkIcon;
    if (status === 'SOURCE') return sourceIcon;
    return healthyIcon;
  };

  const getPolygonStyle = (status, prob) => {
    if (status === 'SINK' || prob >= 0.80) {
      return { fillColor: '#ef4444', color: '#ef4444', weight: 1.5, fillOpacity: 0.25 };
    }
    if (prob >= 0.50) {
      return { fillColor: '#f59e0b', color: '#f59e0b', weight: 1, fillOpacity: 0.15 };
    }
    if (status === 'SOURCE') {
      return { fillColor: '#8b5cf6', color: '#8b5cf6', weight: 1, fillOpacity: 0.1 };
    }
    return { fillColor: '#10b981', color: 'rgba(255,255,255,0.05)', weight: 0.5, fillOpacity: 0.03 };
  };

  return (
    <div className="dashboard-container">
      <div className="sidebar custom-scrollbar" style={{ width: '360px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h1><Zap size={24} color="#8b5cf6" /> <span>MESH</span> CONTROL</h1>
          <button onClick={resetSimulation} className="reset-btn" title="Reset Simulation Data">
            <RotateCcw size={16} />
          </button>
        </div>
        
        <div className="glass-panel">
          <div style={{ fontWeight: 600, fontSize: '13px', textTransform: 'uppercase', tracking: '0.1em', color: 'var(--text-muted)', marginBottom: '12px' }}>
            Scenario Presets
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <button 
              onClick={() => triggerScenario("CRICKET")}
              className={`scenario-btn ${gameState.current_scenario === 'CRICKET' ? 'active-cricket' : ''}`}
            >
              <Trophy size={16} />
              <div style={{ textAlign: 'left' }}>
                <div style={{ fontWeight: 600 }}>Cricket Match Final Overs</div>
                <div style={{ fontSize: '11px', opacity: 0.7 }}>Snack demands surge near Chinnaswamy Stadium</div>
              </div>
            </button>

            <button 
              onClick={() => triggerScenario("MONSOON")}
              className={`scenario-btn ${gameState.current_scenario === 'MONSOON' ? 'active-monsoon' : ''}`}
            >
              <CloudLightning size={16} />
              <div style={{ textAlign: 'left' }}>
                <div style={{ fontWeight: 600 }}>Monsoon Rain Surge</div>
                <div style={{ fontSize: '11px', opacity: 0.7 }}>City-wide rain matrix slows driver speeds by 30%</div>
              </div>
            </button>
          </div>
        </div>

        <div className="glass-panel">
          <div style={{ fontWeight: 600, fontSize: '13px', textTransform: 'uppercase', tracking: '0.1em', color: 'var(--text-muted)', marginBottom: '12px' }}>
            Inject Targeted Demand Spike
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
            {Object.keys(gameState.stores || {}).map((storeId) => {
              const isSpiked = gameState.active_spikes?.includes(storeId);
              return (
                <button
                  key={storeId}
                  onClick={() => toggleManualSpike(storeId)}
                  className={`spike-grid-btn ${isSpiked ? 'active-spike' : ''}`}
                >
                  <Flame size={12} style={{ marginRight: '4px', display: isSpiked ? 'inline' : 'none' }} />
                  {storeId.replace('Store_', '').replace('_', ' ')}
                </button>
              );
            })}
          </div>
        </div>

        <div className="glass-panel" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column', minHeight: '200px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <BrainCircuit size={18} color="#8b5cf6" />
            <span style={{ fontWeight: 600 }}>Cognitive Engine Feed</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '12px', overflowY: 'auto', flexGrow: 1, maxHeight: '250px' }} className="custom-scrollbar">
            {gameState.insights?.map((insight, idx) => (
              <div key={idx} style={{ padding: '8px', backgroundColor: 'rgba(139, 92, 246, 0.05)', borderRadius: '6px', borderLeft: '3px solid #8b5cf6', fontFamily: 'monospace' }}>
                {insight}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="map-container">
        <MapContainer center={BANGALORE_CENTER} zoom={12} zoomControl={false} style={{ height: '100%', width: '100%' }}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          
          {voronoiPolygons.map((poly) => (
            <Polygon 
              key={`poly-${poly.id}`} 
              positions={poly.coords} 
              pathOptions={getPolygonStyle(poly.status, poly.prob)}
              eventHandlers={{ click: () => setSelectedNode(poly.id) }} 
            />
          ))}

          {Object.entries(gameState.stores || {}).map(([id, data]) => {
            if (!data || !data.coords) return null;
            const currentRisk = data.stockout_prob ? (data.stockout_prob * 100).toFixed(0) : "0";
            return (
              <Marker 
                key={id} 
                position={data.coords} 
                icon={getIconForStatus(data.status)} 
                eventHandlers={{ click: () => setSelectedNode(id) }}
              >
                <Tooltip direction="top" offset={[0, -10]} opacity={1}>
                  <div style={{ padding: '2px', color: '#111827', minWidth: '100px' }}>
                    <strong style={{ display: 'block', marginBottom: '2px' }}>
                      {id.replace('Store_', '').replace('_', ' ')}
                    </strong>
                    <span style={{ color: data.status === 'SINK' ? '#ef4444' : '#374151', fontSize: '11px' }}>
                      Max Risk: {currentRisk}%
                    </span>
                  </div>
                </Tooltip>
              </Marker>
            );
          })}

          {(gameState.drivers || []).map((driver, index) => {
            if (!driver || !driver.coords) return null;
            return (
              <Marker key={`driver-${index}`} position={driver.coords} icon={driverIcon}>
                <Tooltip direction="bottom" offset={[0, 10]} opacity={1}>
                  <span style={{ color: '#111827' }}><strong>Agent {driver.id || index}</strong></span>
                </Tooltip>
              </Marker>
            );
          })}

          {(gameState.ghost_routes || []).map((routeCoords, idx) => {
            if (!routeCoords || routeCoords.length < 2) return null;
            return <Polyline key={`ghost-${idx}`} positions={routeCoords} color="#f59e0b" weight={2} dashArray="4, 8" opacity={0.6} />;
          })}

          {(gameState.routes || []).map((routeCoords, idx) => {
            if (!routeCoords || routeCoords.length < 2) return null;
            return <Polyline key={`route-${idx}`} positions={routeCoords} color="#8b5cf6" weight={3.5} dashArray="8, 12" opacity={0.9} />;
          })}

          {Object.keys(gameState.stores || {}).length > 0 && (
            <MapRecenter coords={Object.values(gameState.stores).map(n => n.coords).filter(Boolean)} />
          )}
        </MapContainer>

        {selectedNode && gameState.stores[selectedNode] && (
          <div className="inspector-panel" style={{ zIndex: 2000 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ margin: 0, fontSize: '18px', display: 'flex', alignItems: 'center', gap: '8px', color: '#f0f0f5' }}>
                <Package size={18} color="#8b5cf6" />
                {selectedNode.replace('Store_', '').replace('_', ' ')}
              </h2>
              <button className="close-btn" onClick={() => setSelectedNode(null)} style={{ color: '#8e8e9e', background: 'transparent', border: 'none', cursor: 'pointer' }}>
                <X size={20} />
              </button>
            </div>
            
            <div style={{ display: 'flex', gap: '8px', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '12px', marginTop: '8px', overflowX: 'auto' }} className="custom-scrollbar">
              {Object.keys(gameState.stores[selectedNode].products || {}).map(prod => (
                <button 
                  key={prod}
                  onClick={() => setSelectedProduct(prod)}
                  style={{
                    background: selectedProduct === prod ? 'rgba(139, 92, 246, 0.2)' : 'rgba(255,255,255,0.02)',
                    border: selectedProduct === prod ? '1px solid #8b5cf6' : '1px solid rgba(255,255,255,0.08)', 
                    color: selectedProduct === prod ? '#8b5cf6' : '#8e8e9e',
                    padding: '6px 10px', borderRadius: '6px', cursor: 'pointer', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap'
                  }}
                >
                  <ProductIcon name={prod} />
                  <span>{prod}</span>
                </button>
              ))}
            </div>

            {gameState.stores[selectedNode].products[selectedProduct] ? (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
                  <div style={{ background: 'rgba(0,0,0,0.4)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)' }}>
                    <div style={{ color: '#8e8e9e', fontSize: '11px', textTransform: 'uppercase' }}>Current Stock</div>
                    <div style={{ fontSize: '22px', fontWeight: 'bold', color: '#f0f0f5', marginTop: '4px' }}>
                      {gameState.stores[selectedNode].products[selectedProduct].stock}
                    </div>
                  </div>
                  <div style={{ background: 'rgba(0,0,0,0.4)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)' }}>
                    <div style={{ color: '#8e8e9e', fontSize: '11px', textTransform: 'uppercase' }}>λ Arrival Rate</div>
                    <div style={{ fontSize: '22px', fontWeight: 'bold', color: '#8b5cf6', marginTop: '4px' }}>
                      {gameState.stores[selectedNode].products[selectedProduct].lambda.toFixed(1)}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: '#8e8e9e', textTransform: 'uppercase' }}>Poisson Failure Probability Curve</span>
                    <span style={{ fontSize: '12px', fontWeight: 'bold', color: '#ef4444' }}>
                      {(gameState.stores[selectedNode].stockout_prob * 100).toFixed(0)}% Risk
                    </span>
                  </div>
                  
                  <div style={{ height: '160px', width: '100%', marginLeft: '-20px' }}>
                    <ResponsiveContainer width="110%" height="100%">
                      <AreaChart data={chartData}>
                        <defs>
                          <linearGradient id="colorProb" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.4}/>
                            <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0}/>
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                        <XAxis dataKey="k" stroke="rgba(255,255,255,0.1)" fontSize={10} tick={{fill: '#8e8e9e'}} />
                        <YAxis stroke="rgba(255,255,255,0.1)" fontSize={10} tick={{fill: '#8e8e9e'}} axisLine={false} />
                        <RechartsTooltip 
                          contentStyle={{ backgroundColor: '#15151a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px' }}
                          itemStyle={{ color: '#f0f0f5', fontSize: '12px' }}
                          labelStyle={{ color: '#8e8e9e', fontSize: '11px' }}
                        />
                        <Area type="monotone" dataKey="probability" stroke="#8b5cf6" strokeWidth={2} fillOpacity={1} fill="url(#colorProb)" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  <div style={{ textAlign: 'center', fontSize: '10px', color: '#8e8e9e', marginTop: '6px', fontStyle: 'italic' }}>
                    Cumulative Poisson Model: P(X ≥ Stock)
                  </div>
                </div>
              </>
            ) : (
              <div style={{ color: '#8e8e9e', fontSize: '12px', textAlign: 'center', padding: '20px' }}>
                Select a product category to analyze distribution arrays.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;