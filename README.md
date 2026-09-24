# CXTV Brasil M3U — versão corrigida

Esta versão corrige os dois problemas relatados:

- **Nome correto:** o `tvg-name` e o nome exibido na M3U são obtidos da página individual do canal, priorizando o `h1`/título da própria CXTV. O texto do cartão da listagem não é usado como nome final.
- **Somente canais ativos:** cada URL de mídia é testada antes de entrar na M3U. Para HLS, o teste exige uma playlist `#EXTM3U` com mídia/variantes e valida também uma variante quando disponível.

Também mantém:

- Brasil;
- Português quando a CXTV declara outro idioma, o canal é descartado;
- todas as categorias encontradas;
- uma entrada por categoria;
- remoção de duplicados;
- atualização diária;
- execução manual pelo GitHub Actions.

O workflow falha se nenhum canal ativo for encontrado, evitando publicar uma M3U vazia por engano.
