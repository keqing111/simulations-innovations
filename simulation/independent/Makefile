# build 只编译到独立策略二进制（按身份缓存）；check 先确保二进制存在，
# 再运行 tests/ 下的全部 unittest（真实 CLI、审计篡改、瓶颈匹配穷举）。
.PHONY: check build
build:
	python3 build.py
check: build
	python3 -m unittest discover -s tests -v
