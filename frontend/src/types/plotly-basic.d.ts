// O bundle "basic" (barras, linhas, pizza) tem a mesma API do plotly.js; reaproveita os tipos de @types/plotly.js.
declare module "plotly.js-basic-dist-min" {
  import Plotly from "plotly.js";
  export * from "plotly.js";
  export default Plotly;
}
