// Builds ui/geo.json (country and US state outlines) from the world-atlas and
// us-atlas npm packages. Run: npm i world-atlas us-atlas topojson-client, then
//   NODE_PATH=node_modules node pipeline/build_geo.js
const fs = require("fs"), path = require("path");
const topo = require("topojson-client");
const world = require("world-atlas/countries-50m.json");
const us = require("us-atlas/states-10m.json");
const round = (g) => JSON.parse(JSON.stringify(g, (k, v) => (typeof v === "number" ? Math.round(v * 100) / 100 : v)));
const out = {
  land: round(topo.feature(world, world.objects.land)),
  borders: round(topo.mesh(world, world.objects.countries, (a, b) => a !== b)),
  states: round(topo.mesh(us, us.objects.states, (a, b) => a !== b)),
};
const file = path.join(__dirname, "..", "ui", "geo.json");
fs.writeFileSync(file, JSON.stringify(out));
console.log(file, Math.round(fs.statSync(file).size / 1024) + " KB");
