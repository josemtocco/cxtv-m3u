# CXTV Brasil M3U — versão corrigida

A versão anterior podia gerar uma M3U vazia porque o HTML recebido pelo `aiohttp` não continha o stream: a página da CXTV monta o player dinamicamente. Esta versão usa Chromium/Playwright.

## Fluxo

1. Abre `https://www.cxtv.com.br/tv/paises/tvs-brasil` em Chromium.
2. Clica em `Carregar Mais` enquanto o botão existir.
3. Coleta os canais.
4. Abre cada página de canal.
5. Captura URLs de `iframe`, `video`, `source` e recursos de rede (`m3u8`, `mp4`, `ts`).
6. Recupera todas as categorias.
7. Testa cada stream.
8. Remove os que falham.
9. Gera uma entrada por categoria.
10. Atualiza diariamente pelo GitHub Actions.

O workflow falha se não encontrar streams, evitando publicar silenciosamente uma lista vazia.
