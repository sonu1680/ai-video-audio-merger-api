.PHONY: dev clean

dev:
	uvicorn server:app --host 0.0.0.0 --port 8000 --reload

clean:
	@echo "Cleaning up..."
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@rm -f videos/*.mp4
	@echo "Cleanup complete."