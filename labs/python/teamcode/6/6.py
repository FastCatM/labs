n=int(input())
if n<0:
 n*=-1
 print('-',str(n)[::-1],sep='')
else:
    print(str(n)[::-1])