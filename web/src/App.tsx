import { useEffect, useState } from "react";
import { parseHash, type Route } from "./routes";

// Shell: renders only the route's name. Later tasks replace the body.
export function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return <main data-route={route.name}>{route.name}</main>;
}
