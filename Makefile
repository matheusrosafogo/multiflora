SHELL := /bin/bash

PWD := $(shell pwd)

TARGET ?= arista
USER ?= matheus
DATA ?= data.yaml
INVENTORY ?= inventory.yaml
TEMPLATE ?= template.j2
COMMAND ?=
# NOTA (LTO): com o clang 14 + ld.gold deste container, --lto=yes quebra o link
# do onefile ("LLVM gold plugin: Could not find incbin file 'blobs/__constant.bin'").
# Por isso o valor é fixo em 'no'; se o toolchain mudar, altere aqui.

.PHONY: build prepare app docs gui delete stage prepare exec command destroy dry-run deploy clean_ssh clean

# MODO ONEFILE (EXECUTÁVEL ÚNICO)
# NOTA: NÃO usar --pgo-python aqui. Ele compila um binário auxiliar e o executa
# durante o build para coletar o perfil; como o gui.py termina em mainloop(),
# essa execução nunca retorna e o build trava. (A opção também está marcada como
# "not yet ready" no próprio Nuitka.)
onefile:
	. .venv/bin/activate && cd bin && python3 -m nuitka --mode=onefile --output-dir=../multiflora --output-filename=multiflora --lto=no --clang --python-flag=-O --python-flag=no_site --enable-plugin=anti-bloat --enable-plugin=tk-inter --include-package=scrapli --include-package=asyncssh --include-package=jinja2 --include-package=yaml --include-data-file=arg_parser.py=arg_parser.py   --include-data-file=execution_timer.py=execution_timer.py   --include-data-file=logger.py=logger.py   --include-data-file=main.py=main.py --include-data-files=assets/multiflora.png=assets/multiflora.png gui.py
# MODO STANDALONE (PASTA COM DEPENDÊNCIAS)
standalone:
	. .venv/bin/activate && cd bin && python3 -m nuitka --standalone --enable-plugin=tk-inter --include-package=scrapli --include-package=asyncssh --include-package=jinja2 --include-package=yaml --include-data-file=arg_parser.py=arg_parser.py   --include-data-file=execution_timer.py=execution_timer.py   --include-data-file=logger.py=logger.py   --include-data-file=main.py=main.py --include-data-files=assets/multiflora.png=assets/multiflora.png gui.py

# EXECUTAR A APLICAÇÃO SEM COMPILAR
app:
	rm -rf .venv
	deactivate || true
	uv venv .venv
	uv pip install -r requirements.txt
	. .venv/bin/activate && python bin/gui.py

# CRIAR A DOCUMENTAÇÃO
docs:
	pdoc bin/main.py --output-directory docs/main
	pdoc bin/logger.py --output-directory docs/logger
	pdoc bin/gui.py --output-directory docs/gui
	pdoc bin/execution_timer.py --output-directory docs/execution_timer
	pdoc bin/arg_parser.py --output-directory docs/arg_parser
	pdoc bin/render_pool/renderer.py --output-directory docs/render_pool/renderer
	pdoc bin/render_pool/path_solver.py --output-directory docs/render_pool/path_solver
	pdoc bin/render_pool/jinja_env.py --output-directory docs/render_pool/jinja_env
	pdoc bin/event_pool/s_handler.py --output-directory docs/event_pool/s_handler

# EXECUTAR A INTERFACE GRÁFICA
gui:
	python bin/gui.py

# DELETAR .VENV
delete:
	rm -rf .venv
	echo "Deactivate the virtual env with: 'deactivate'"

# LIMPAR ARQUIVOS DE BUILD E DIST
stage:
	rm -rf bin/gui.dist
	rm -rf bin/gui.build

# CRIAR VIRTUAL ENV E INSTALAR DEPENDÊNCIAS
prepare:
	rm -rf .venv
	uv venv .venv
	uv pip install -r requirements.txt

# EXECUTAR TOPOLOGIA NO CONTAINER DO CONTAINERLAB (DOCKER COMPOSE)
exec:
	sudo docker exec -it -w $${PWD} containerlab containerlab deploy -t bin/config/$(TARGET)/clab/$(TARGET).clab.yaml --reconfigure

# EXECUTAR COMANDO NO CONTAINER DO CONTAINERLAB (DOCKER COMPOSE)
command:
	sudo docker exec -it -w $${PWD} containerlab $(COMMAND)

# DESTRUIR TOPOLOGIA NO CONTAINER DO CONTAINERLAB (DOCKER COMPOSE)
destroy:
	sudo docker exec -it -w $${PWD} containerlab containerlab destroy -t bin/config/$(TARGET)/clab/$(TARGET).clab.yaml

# EXECUTAR BACKEND SEM A GUI NO MODO DRY-RUN
dry-run:
	python bin/main.py config/$(TARGET)/ config/$(TARGET)/$(INVENTORY) config/$(TARGET)/$(DATA) config/$(TARGET)/$(TEMPLATE) --dry-run	

# EXECUTAR BACKEND SEM A GUI NO MODO DEPLOY
deploy:
	python bin/main.py config/$(TARGET)/ config/$(TARGET)/$(INVENTORY) config/$(TARGET)/$(DATA) config/$(TARGET)/$(TEMPLATE) --deploy

# LIMPAR CHAVES SSH DE HOSTS REMOVIDOS (IPS DAS TOPOLOGIAS DE DEMONSTRAÇÃO)
clean_ssh:
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.99.0.2"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.99.0.3"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.99.0.4"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.99.0.5"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.98.0.2"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.98.0.3"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.97.0.2"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.97.0.3"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.97.0.4"
	ssh-keygen -f /home/$(USER)/.ssh/known_hosts -R "10.97.0.5"

# LIMPAR ARQUIVOS DE CONFIGURAÇÃO E LOGS GERADOS PELA APLICAÇÃO
clean:
	rm -rf config/$(TARGET)/cfg && rm -rf config/$(TARGET)/logs