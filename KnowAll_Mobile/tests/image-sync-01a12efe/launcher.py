"""Keep API imports out of the renderer child's __mp_main__, like uvicorn's launcher."""
if __name__ == '__main__':
    import server
    import uvicorn
    uvicorn.run(server.app,host='127.0.0.1',port=server.args.port,log_level='warning')
