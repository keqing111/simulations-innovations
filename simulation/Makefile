.PHONY: build run clean debug plot check

TARGET = all2all_simulator
CC = g++
CFLAGS = -O3 -std=c++11
TEST_DIR = $(shell pwd)
BUILD_DIR = $(shell pwd)/build
ALGO_DIR = $(shell pwd)/algorithm
SCHE_DIR = $(shell pwd)/scheduler

INCLUDES = -I$(shell pwd)
INCLUDES += -I$(ALGO_DIR)
INCLUDES += -I$(SCHE_DIR)

OBJS = $(BUILD_DIR)/matrix.o
OBJS += $(BUILD_DIR)/all2all.o
OBJS += $(BUILD_DIR)/test.o
OBJS += $(BUILD_DIR)/config.o
OBJS += $(BUILD_DIR)/local.o
OBJS += $(BUILD_DIR)/global.o
OBJS += $(BUILD_DIR)/topology.o


LD = -lpthread

SOURCES = main.cpp

build: main.cpp define.h $(OBJS)
	@echo "Building FastAll2All"
	@if [ ! -d "$(BUILD_DIR)" ]; then \
		echo "creating build directory"; \
		mkdir $(BUILD_DIR);\
	fi
	$(CC) -g $(CFLAGS) $(INCLUDES) $(OBJS) $(SOURCES) -o $(BUILD_DIR)/$(TARGET) $(LD)
	@echo "Building successful"

$(BUILD_DIR)/all2all.o: $(BUILD_DIR)/matrix.o ${ALGO_DIR}/all2all.cpp $(ALGO_DIR)/all2all.h topology/model.h define.h
	$(CC) $(CFLAGS) $(INCLUDES) -c ${ALGO_DIR}/all2all.cpp -o $(BUILD_DIR)/all2all.o

$(BUILD_DIR)/matrix.o: ${ALGO_DIR}/matrix.cpp $(ALGO_DIR)/matrix.h define.h
	$(CC) $(CFLAGS) $(INCLUDES) -c ${ALGO_DIR}/matrix.cpp -o $(BUILD_DIR)/matrix.o

$(BUILD_DIR)/test.o: $(BUILD_DIR)/all2all.o $(BUILD_DIR)/matrix.o test.cpp test.h define.h $(BUILD_DIR)/config.o $(BUILD_DIR)/local.o $(BUILD_DIR)/global.o
	$(CC) $(CFLAGS) $(INCLUDES) -c test.cpp -o $(BUILD_DIR)/test.o

$(BUILD_DIR)/config.o: $(SCHE_DIR)/config.cpp $(SCHE_DIR)/config.h define.h
	$(CC) $(CFLAGS) $(INCLUDES) -c $(SCHE_DIR)/config.cpp -o $(BUILD_DIR)/config.o

$(BUILD_DIR)/local.o: $(BUILD_DIR)/config.o $(SCHE_DIR)/local.cpp $(SCHE_DIR)/local.h define.h
	$(CC) $(CFLAGS) $(INCLUDES) -c $(SCHE_DIR)/local.cpp -o $(BUILD_DIR)/local.o

$(BUILD_DIR)/global.o: $(BUILD_DIR)/config.o  $(BUILD_DIR)/matrix.o $(BUILD_DIR)/all2all.o $(SCHE_DIR)/global.cpp $(SCHE_DIR)/global.h topology/model.h define.h
	$(CC) $(CFLAGS) $(INCLUDES) -c $(SCHE_DIR)/global.cpp -o $(BUILD_DIR)/global.o

$(BUILD_DIR)/topology.o: topology/model.cpp topology/model.h
	$(CC) $(CFLAGS) $(INCLUDES) -c topology/model.cpp -o $@

$(BUILD_DIR):
	mkdir -p $@

$(OBJS): | $(BUILD_DIR)

$(BUILD_DIR)/test_topology_schedule: tests/test_topology_schedule.cpp $(filter-out $(BUILD_DIR)/test.o,$(OBJS))
	$(CC) $(CFLAGS) $(INCLUDES) $^ -o $@ $(LD)

check: build $(BUILD_DIR)/test_topology_schedule
	$(BUILD_DIR)/test_topology_schedule
	python3 -m unittest discover -s tests -v

run:
	$(BUILD_DIR)/$(TARGET)

clean:
	@echo "Cleaning FastAll2All"
	rm -rf $(BUILD_DIR)
	mkdir $(BUILD_DIR)

debug:
	sudo gdb --args $(BUILD_DIR)/$(TARGET)

PLOT_DIR := $(shell pwd)/plot
plot: FORCE
	$(shell cd plot && python3 plot_server_gpu_number.py && python3 plot_intra_speed_skewness.py)

FORCE: ;

# Independent BvN entry: no Local/Global scheduling or historical drivers.
.PHONY: bvn-build bvn-check
BVN_OBJS = $(BUILD_DIR)/bvn_schedule.o $(BUILD_DIR)/matrix.o $(BUILD_DIR)/all2all.o $(BUILD_DIR)/config.o $(BUILD_DIR)/topology.o
BVN_REVISION := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
BVN_DIRTY := $(shell if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then if test -n "$$(git status --porcelain 2>/dev/null)"; then echo true; else echo false; fi; else echo unknown; fi)

$(BUILD_DIR)/bvn_schedule.o: algorithm/bvn_schedule.cpp algorithm/bvn_schedule.h algorithm/all2all.h algorithm/matrix.h topology/model.h | $(BUILD_DIR)
	$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(BUILD_DIR)/bvn_simulator: bvn_main.cpp algorithm/bvn_schedule.h $(BVN_OBJS) FORCE
	$(CC) $(CFLAGS) $(INCLUDES) -DBVN_GIT_REVISION='"$(BVN_REVISION)"' -DBVN_GIT_DIRTY='"$(BVN_DIRTY)"' bvn_main.cpp $(BVN_OBJS) -o $@ $(LD)

$(BUILD_DIR)/test_bvn_schedule: tests/test_bvn_schedule.cpp algorithm/bvn_schedule.h $(BVN_OBJS)
	$(CC) $(CFLAGS) $(INCLUDES) tests/test_bvn_schedule.cpp $(BVN_OBJS) -o $@ $(LD)

bvn-build: $(BUILD_DIR)/bvn_simulator

bvn-check: bvn-build $(BUILD_DIR)/test_bvn_schedule
	$(BUILD_DIR)/test_bvn_schedule --cli

# Complete padded BvN strategies, using the unchanged FAST pipeline components.
.PHONY: bvn-decompose-build
$(BUILD_DIR)/bvn_decompose_simulator: bvn_decompose_main.cpp algorithm/all2all.h scheduler/global.h $(filter-out $(BUILD_DIR)/test.o,$(OBJS))
	$(CC) $(CFLAGS) $(INCLUDES) bvn_decompose_main.cpp $(filter-out $(BUILD_DIR)/test.o,$(OBJS)) -o $@ $(LD)

bvn-decompose-build: $(BUILD_DIR)/bvn_decompose_simulator

# Unmodified NVIDIA CPU volume planner, not the simulation GlobalScheduler.
CUDA_INCLUDE ?= /usr/local/cuda/include
NATIVE_DIR = ../nvidia/src
NATIVE_CPU = $(addprefix $(NATIVE_DIR)/fast_alltoall/,alltoall_matrix.cc alltoall_algorithm.cc alltoall_local_scheduler.cc alltoall_global_scheduler.cc)
.PHONY: runtime-host-build
$(BUILD_DIR)/runtime_host_reference: ../ds/decomposition/runtime_host.cpp $(NATIVE_CPU) $(wildcard $(NATIVE_DIR)/include/fast_alltoall/*.h) | $(BUILD_DIR)
	$(CC) $(CFLAGS) -DNOLOG -ffunction-sections -fdata-sections -I$(NATIVE_DIR)/include -I$(CUDA_INCLUDE) $< $(NATIVE_CPU) -Wl,--gc-sections -o $@

runtime-host-build: $(BUILD_DIR)/runtime_host_reference
