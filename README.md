# CXTV Brasil M3U

Gerador automático de playlist M3U a partir da CXTV Brasil.

## Recursos

- percorre a listagem brasileira e tenta carregar todas as páginas disponíveis;
- acompanha links de canais encontrados em paginação e em "Carregar Mais";
- recupera nome, idioma e todas as categorias do canal;
- gera uma entrada por categoria, mantendo o mesmo stream;
- testa os streams individualmente;
- remove automaticamente streams que falharem;
- remove duplicados;
- gera `listas/cxtv-brasil.m3u`;
- atualização automática pelo GitHub Actions;
- execução manual pelo GitHub Actions.

## Fonte

https://www.cxtv.com.br/tv/paises/tvs-brasil

## URL da lista

Depois do primeiro workflow, use:

https://raw.githubusercontent.com/SEU-USUARIO/SEU-REPOSITORIO/main/listas/cxtv-brasil.m3u
